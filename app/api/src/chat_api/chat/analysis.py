"""
chat.analysis: data analysis in two steps, fetch then compute.

- `get_case_data` runs in the API process. It returns raw rows from a fixed catalog of
  case-study datasets with simple equality filters; the SQL is built here, never by the
  model. The rows are kept for the turn under a handle (`d1`, `d2`, ...).
- `run_python_analysis` runs the model's Python in a throwaway subprocess with those
  datasets handed in as pandas DataFrames (`data["d1"]`), and returns what it prints.
  The subprocess has no database: grouping, rates and budget math are the Python's job.

Used for exploratory or what-if questions. It never allocates budget or commits a plan.

Limits are assessment-grade, not a security boundary (CPython isn't one):
- a wall-clock timeout (the process group is killed), plus an RLIMIT_CPU backstop;
- stdout/stderr capped at `output_cap` bytes each, the rest drained and dropped;
- a minimal environment, so no API keys reach the child;
- in the docker image (the API runs as root) the child drops to the unprivileged
  `sandbox` user, which can't read the chat database or the API's environment,
  and gets memory, file-size, file-count and process caps;
- the network is NOT blocked: that would need namespaces or a separate container.

Running locally as a normal user (e.g. macOS dev), privileges aren't dropped and
only the timeout, output caps and clean environment apply.
"""

from __future__ import annotations

from chat_api.workflow.policy import inventory_policy
import asyncio
import json
import logging
import os
import pwd
import resource
import shutil
import signal
import sqlite3
import sys
import tempfile
import time
from contextlib import closing
from pathlib import Path
from typing import Callable

from vel.tools.registry import ToolSpec  # type: ignore

from chat_api.casedata import connect_readonly, ensure_case_db

logger = logging.getLogger(__name__)

RUNNER_PATH = Path(__file__).resolve().with_name("sandbox_runner.py")
DEFAULT_TIMEOUT_S = 5.0
OUTPUT_CAP_BYTES = 32_768
MAX_CODE_CHARS = 20_000
SANDBOX_USER = os.getenv("APP_SANDBOX_USER", "sandbox")

_warned_no_drop = False


def _sandbox_ids() -> tuple[int, int] | None:
    """(uid, gid) to run the child as, or None when privileges can't be dropped."""
    global _warned_no_drop
    if os.geteuid() != 0:
        if not _warned_no_drop:
            logger.warning("run_python_analysis: not root, so analysis runs as the API user (dev only)")
            _warned_no_drop = True
        return None
    entry = pwd.getpwnam(SANDBOX_USER)  # KeyError if the image lacks the user: fail loudly
    return entry.pw_uid, entry.pw_gid


def _prepare_child(timeout: float, ids: tuple[int, int] | None) -> Callable[[], None]:
    """Runs in the child before exec: resource caps, then the privilege drop.

    The drop happens here rather than through Popen's `user`/`group` arguments
    because uvicorn runs on uvloop, whose subprocess_exec rejects those arguments.
    """
    dropping = ids is not None

    def apply() -> None:
        caps = [(resource.RLIMIT_CPU, int(timeout) + 1)]
        if dropping:
            # Only for the sandbox user: on a dev machine these would count the
            # developer's own processes (NPROC) or aren't supported (AS on macOS).
            caps += [
                (resource.RLIMIT_AS, 1 << 30),
                (resource.RLIMIT_FSIZE, 1 << 20),
                (resource.RLIMIT_NOFILE, 64),
                (resource.RLIMIT_NPROC, 8),  # Linux counts threads too; numpy is pinned to 1
            ]
        for which, value in caps:
            try:
                resource.setrlimit(which, (value, value))
            except (ValueError, OSError):
                pass
        if ids:
            uid, gid = ids
            os.setgroups([])
            os.setgid(gid)
            os.setuid(uid)  # last: after this the child can't regain root

    return apply


def _child_env(workspace: Path) -> dict[str, str]:
    """Built from scratch, never copied from os.environ, so no secret is inherited."""
    return {
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "HOME": str(workspace),
        "LANG": "C.UTF-8",
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


async def _read_capped(stream: asyncio.StreamReader, cap: int) -> tuple[bytes, bool]:
    """Keep the first `cap` bytes; drain and drop the rest so the child never blocks."""
    kept = bytearray()
    over = False
    while chunk := await stream.read(65_536):
        room = cap - len(kept)
        if len(chunk) > room:
            over = True
        if room > 0:
            kept += chunk[:room]
    return bytes(kept), over


def _clean_stderr(text: str) -> str:
    """The model's own stderr prints, minus anything traceback-shaped (e.g. from the
    interpreter itself on a fatal error)."""
    if "Traceback" in text or 'File "' in text:
        lines = [line for line in text.strip().splitlines() if line.strip()]
        return lines[-1] if lines else ""
    return text


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


async def run_analysis(
    code: str,
    *,
    datasets: dict[str, list[dict]],
    timeout: float = DEFAULT_TIMEOUT_S,
    output_cap: int = OUTPUT_CAP_BYTES,
    on_spawn: Callable[[int, Path], None] | None = None,
) -> dict:
    """Run `code` over `datasets` (id -> rows) in a bounded subprocess and return the
    result dict. The code sees them as `data[id]`, pandas DataFrames.

    `on_spawn(pid, workspace)` is a test hook, called once the child is running.
    """
    started = time.monotonic()
    ids = _sandbox_ids()
    workspace = Path(tempfile.mkdtemp(prefix="analysis-"))
    proc: asyncio.subprocess.Process | None = None
    try:
        (workspace / "analysis.py").write_text(code, encoding="utf-8")
        (workspace / "datasets.json").write_text(json.dumps(datasets), encoding="utf-8")
        shutil.copyfile(RUNNER_PATH, workspace / "runner.py")
        if ids:
            for path in (
                workspace,
                workspace / "analysis.py",
                workspace / "datasets.json",
                workspace / "runner.py",
            ):
                os.chown(path, *ids)
            workspace.chmod(0o700)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "runner.py",
            cwd=workspace,
            env=_child_env(workspace),
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,  # its own process group, so a kill takes any children too
            preexec_fn=_prepare_child(timeout, ids),
        )
        if on_spawn:
            on_spawn(proc.pid, workspace)

        timed_out = False
        try:
            (stdout, out_over), (stderr, _) = await asyncio.wait_for(
                asyncio.gather(
                    _read_capped(proc.stdout, output_cap),
                    _read_capped(proc.stderr, output_cap),
                ),
                timeout,
            )
            await proc.wait()
        except asyncio.TimeoutError:
            timed_out = True
            _kill_group(proc.pid)
            await proc.wait()
            stdout, out_over, stderr = b"", False, b""

        meta: dict = {}
        try:
            meta = json.loads((workspace / ".meta.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass

        error = meta.get("error")
        if timed_out:
            error = f"Timed out after {timeout:g}s"
        elif not error and proc.returncode != 0:
            # Killed by a resource limit (e.g. SIGXCPU, SIGKILL on memory) before writing meta.
            error = f"Analysis process exited with status {proc.returncode}"
        return {
            "success": not timed_out and not error and proc.returncode == 0,
            "stdout": stdout.decode("utf-8", errors="replace"),
            "stderr": _clean_stderr(stderr.decode("utf-8", errors="replace")),
            "error": error,
            "timed_out": timed_out,
            "truncated": out_over,
            "duration_ms": int((time.monotonic() - started) * 1000),
            "datasets_used": sorted(datasets),
        }
    finally:
        # Also runs on cancellation (the user pressed Stop): no child outlives the call.
        if proc is not None and proc.returncode is None:
            _kill_group(proc.pid)
            try:
                await asyncio.shield(proc.wait())
            except asyncio.CancelledError:
                pass
        shutil.rmtree(workspace, ignore_errors=True)


# --- get_case_data: the fixed catalog -----------------------------------------------

# dataset -> (table, columns in source order, filters it allows). Raw rows only: no
# computed columns and no views, so the analysis happens in Python, where it's visible.
CATALOG: dict[str, tuple[str, tuple[str, ...], tuple[str, ...]]] = {
    "products": (
        "products",
        ("product_id", "product_name", "product_description", "cpm"),
        ("product_id",),
    ),
    "campaign_history": (
        "campaign_line_items",
        ("product_id", "vertical", "impressions", "clicks", "viewable_impressions"),
        ("product_id", "vertical"),
    ),
    "inventory": (
        "inventory",
        ("product_id", "vertical", "geo", "available_imps", "inventory_risk"),
        ("product_id", "vertical", "geo"),
    ),
    "client_briefs": ("client_briefs", ("brief_id", "text"), ()),
}
PREVIEW_ROWS = 5

# What each dataset means, for the model. Rendered with the columns and filters above into
# `case_data_schema()`, which goes into both the system prompt and get_case_data's
# description, so the model knows the columns (and what's absent, like geo) before it calls.
DATASET_NOTES = {
    "products": "The catalog; cpm is USD per 1,000 impressions",
    "campaign_history": (
        "One row per historical line item. It has NO geo: performance isn't split by region, so "
        "for a geo-specific question (\"US CTR\") use the vertical already in the conversation "
        "(e.g. the client's vertical from an earlier message), or all verticals if none was "
        "given, and mention in one line that the benchmark isn't geo-specific"
    ),
    "inventory": (
        "Forecast scale per product, vertical and geo; inventory_risk is forecast CONFIDENCE "
        "0..1 (higher = more reliable)"
    ),
    "client_briefs": "The sample client briefs, as plain text",
}
DATASET_VALUES = (
    "verticals Retail, QSR, Finance, Entertainment, Travel; geos US, EMEA, APAC (inventory only); "
    "products P001-P006"
)


def case_data_schema() -> str:
    """The datasets get_case_data offers: columns, filters and meaning, one line each."""
    lines = []
    for name, (_table, columns, filters) in CATALOG.items():
        offered = ", ".join(filters) if filters else "none"
        lines.append(f"- {name}({', '.join(columns)}). Filters: {offered}. {DATASET_NOTES[name]}.")
    lines.append(f"Values: {DATASET_VALUES}.")
    return "\n".join(lines)


def _get_case_data(inp: dict, ctx: dict) -> dict:
    dataset = str(inp.get("dataset") or "")
    if dataset not in CATALOG:
        return {"error": f"Unknown dataset {dataset!r}. Choose one of: {', '.join(CATALOG)}."}
    table, columns, allowed = CATALOG[dataset]
    filters = inp.get("filters") or {}
    if not isinstance(filters, dict):
        return {"error": "filters must be an object, e.g. {\"vertical\": \"QSR\"}."}
    bad = [key for key in filters if key not in allowed]
    if bad:
        offered = ", ".join(allowed) or "none"
        message = f"{dataset} can't be filtered by {', '.join(bad)}. Filters it offers: {offered}."
        if "geo" in bad and dataset != "inventory":
            message += (
                " Historical performance and the catalog aren't broken out by geo (geo exists only"
                " in inventory): drop the geo filter, and mention that in one line."
            )
        return {"error": message}

    where, params = [], []
    for key, value in filters.items():
        values = value if isinstance(value, list) else [value]
        values = [str(v) for v in values]
        if not values:
            continue
        where.append(f"{key} IN ({', '.join('?' for _ in values)})")
        params.extend(values)
    sql = f"SELECT {', '.join(columns)} FROM {table}"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY rowid"
    try:
        with closing(connect_readonly(ensure_case_db())) as db:
            rows = [dict(row) for row in db.execute(sql, params).fetchall()]
    except (FileNotFoundError, sqlite3.Error) as exc:
        return {"error": f"Case data unavailable: {exc}"}

    store = ctx.setdefault("datasets", {})
    dataset_id = f"d{len(store) + 1}"
    store[dataset_id] = rows
    return {
        "dataset_id": dataset_id,
        "dataset": dataset,
        "filters": filters,
        "row_count": len(rows),
        "columns": list(columns),
        "preview": rows[:PREVIEW_ROWS],
    }


GET_CASE_DATA = ToolSpec(
    name="get_case_data",
    description=(
        "Fetch raw rows from the case data for analysis. Returns a dataset_id (d1, d2, ...), "
        "the row count, the columns and a short preview; pass the dataset_id to "
        "run_python_analysis to compute with the full rows. You never write SQL: pick a dataset "
        "and optional equality filters (a value or a list of values).\n\n"
        "Datasets:\n" + case_data_schema() + "\n\n"
        "The preview is for orientation only: compute numbers in run_python_analysis, not from it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "dataset": {"type": "string", "enum": list(CATALOG)},
            "filters": {
                "type": "object",
                "description": "Equality filters, e.g. {\"vertical\": \"QSR\", \"geo\": \"US\"} or {\"product_id\": [\"P002\", \"P004\"]}.",
                "properties": {
                    key: {
                        "anyOf": [
                            {"type": "string"},
                            {"type": "array", "items": {"type": "string"}},
                        ]
                    }
                    for key in ("product_id", "vertical", "geo")
                },
                "additionalProperties": False,
            },
        },
        "required": ["dataset"],
    },
    output_schema={"type": "object"},
    handler=_get_case_data,
    parallel_safe=True,
)


# --- run_python_analysis --------------------------------------------------------------

_INV = inventory_policy()  # the recommendation workflow's inventory rule (workflow/policy.py)

RECIPES = f"""\
Recipes (adapt them; data["d1"] etc. are the datasets you fetched):
    h = data["d1"]                                   # campaign_history (e.g. vertical QSR)
    b = h.groupby("product_id")[["impressions", "clicks", "viewable_impressions"]].sum()
    b["ctr"] = b.clicks / b.impressions              # ratio of sums, never the mean of per-row rates
    b["in_view_rate"] = b.viewable_impressions / b.impressions
    b = b.join(data["d2"].set_index("product_id")[["product_name", "cpm"]])   # products
    b["ecpc"] = b.cpm / (1000 * b.ctr)               # USD per click
    b["vcpm"] = b.cpm / b.in_view_rate               # USD per 1,000 viewable impressions
    budget = 15000                                   # what a budget buys
    b["impressions_bought"] = (budget / b.cpm * 1000 + 1e-9).astype(int)
    b["clicks_expected"] = b.impressions_bought * b.ctr
    b["viewable_expected"] = b.impressions_bought * b.in_view_rate
    inv = data["d3"].set_index("product_id")         # inventory for the vertical and geo
    b = b.join(inv[["available_imps", "inventory_risk"]])
    b["usable_imps"] = {_INV.pandas_expr("b")}   # the inventory rule: {_INV.rule()}
    b["inventory_coverage"] = b.usable_imps / b.impressions_bought   # < {_INV.min_absorption:g} = can't absorb the budget
    out = b.sort_values("ctr", ascending=False)      # present: rates as %, the rest as is
    out = out[["product_name", "cpm", "ctr", "in_view_rate", "ecpc", "vcpm", "impressions_bought", "available_imps", "usable_imps", "inventory_coverage", "inventory_risk"]].copy()
    for col in ["ctr", "in_view_rate", "inventory_coverage"]:
        out[col] = (out[col] * 100).map("{{:.2f}}%".format)
    out
"""

CONTEXT_RECIPE = """\
Context for a vertical's numbers (fetch campaign_history for ALL verticals as, say, d4):
    a = data["d4"]
    other = a[a.vertical != "QSR"].groupby("product_id")[["impressions", "clicks", "viewable_impressions"]].sum()  # all but the client's vertical
    b["ctr_other_verticals"] = other.clicks / other.impressions          # the product's usual level
    b["in_view_other_verticals"] = other.viewable_impressions / other.impressions
    li = h.assign(ivr=h.viewable_impressions / h.impressions).groupby("product_id")["ivr"]
    b["line_items"], b["in_view_min"], b["in_view_max"] = li.size(), li.min(), li.max()   # how consistent
"""

RECIPE_NOTES = f"""\
Inventory: report the forecast (available_imps), its confidence (inventory_risk, higher = more
reliable) and usable inventory under the inventory rule, the one the recommendation workflow uses:
{_INV.rule()}. Coverage = usable / impressions bought; below {_INV.min_absorption:.0%} the product
can't absorb the budget on its own, so say so.
Compute with fractions (ctr 0.0115); convert rates to percentages only in the final
presentation step, as above. Don't pass your own float_format: the default display is
already readable.
Meanings: ctr is the KPI "click-through rate"; in_view_rate is "in-view rate"; a viewable
impression is NOT a click. Lower ecpc / vcpm is more efficient per dollar. Group by
["product_id", "vertical"] to compare verticals."""


async def _run_python_analysis(inp: dict, ctx: dict) -> dict:
    code = str(inp.get("code") or "")
    if not code.strip():
        return {"success": False, "error": "code is required"}
    if len(code) > MAX_CODE_CHARS:
        return {"success": False, "error": f"code is longer than {MAX_CODE_CHARS} characters"}
    store = (ctx or {}).get("datasets") or {}
    wanted = [str(d) for d in (inp.get("datasets") or [])]
    unknown = [d for d in wanted if d not in store]
    if unknown:
        return {
            "success": False,
            "error": (
                f"Unknown dataset id {unknown[0]!r}. Call get_case_data first and pass the "
                "dataset_id it returns."
            ),
        }
    logger.info("run_python_analysis: %s", str(inp.get("question") or "")[:200])
    return await run_analysis(code, datasets={d: store[d] for d in wanted})


def run_python_analysis_tool(*, context_recipe: bool = True) -> ToolSpec:
    """The tool, optionally without the context recipe (for prompt ablations)."""
    recipes = RECIPES + (CONTEXT_RECIPE if context_recipe else "") + RECIPE_NOTES
    return ToolSpec(
        name="run_python_analysis",
        description=(
            "Run a short Python program and return what it prints. Mainly for analysing the case data: "
            "fetch it with get_case_data, then compute here over the datasets you pass (benchmarks, "
            "budget math, inventory checks); that output is analysis, never a committed plan. It also "
            "runs any other Python the user asks for (e.g. try out a function, check a calculation); "
            "then pass no datasets.\n\n"
            "In scope: `data` (dict of dataset_id -> pandas DataFrame, only the ids you pass in "
            "`datasets`; empty if none) and `pd`; the standard library is available too. There is no database here and no SQL: do the computation in "
            "pandas. Use the full rows in `data`; never type numbers from a preview into the code. "
            "Print (or end on) a concise, decision-relevant result, never whole raw tables. No files, "
            "no network, no package installs; about 5 seconds of runtime.\n\n" + recipes
        ),
        input_schema={
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "Python (pandas) analysis over data[...]. Print a concise result.",
                },
                "datasets": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "dataset_ids from get_case_data in this turn, e.g. [\"d1\", \"d2\"]. Omit for code that doesn't use the case data.",
                },
                "question": {
                    "type": "string",
                    "description": "The user's analytical question, for logging and to orient the analysis.",
                },
            },
            "required": ["code", "question"],
        },
        output_schema={"type": "object"},
        handler=_run_python_analysis,
        parallel_safe=True,
    )


# Without the context recipe: the prompt ablation found it didn't help (V2 is the shipped variant).
RUN_PYTHON_ANALYSIS = run_python_analysis_tool(context_recipe=False)
