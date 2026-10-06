"""Run the labelled eval across variants and trials; write results and a report.

    uv run python -m evals.run_eval --variants v1,v2,v3 --k 5
    uv run python -m evals.run_eval --only brief1_acme,greeting --k 1   # smoke test

Needs OPENAI_API_KEY (environment or .env). Writes results/<timestamp>/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

from wt import upstream
from wt.variants import VARIANTS, Conversation, Settings, TurnRecord, run_turn
from wt.workflow_tool import TOOL_NAME, upstream_client_factory

ROOT = Path(__file__).resolve().parents[1]
EVALSET = ROOT / "evals" / "evalset.yaml"
# USD per 1M tokens (input, output); override with --price-in/--price-out
DEFAULT_PRICES = {"gpt-4o-mini": (0.15, 0.60), "gpt-4.1-nano": (0.10, 0.40), "gpt-4.1-mini": (0.40, 1.60)}
ASK_KEYWORDS = {
    "vertical": ("vertical", "industry", "category", "sector", "type of business", "kind of business", "what kind of"),
    "kpi": ("kpi", "click-through", "click through", "ctr", "in-view", "in view", "viewab", "optimi", "metric", "objective"),
    "geo": ("geo", "region", "market", "where", "location", "country", "us, emea", "emea"),
    "budget": ("budget", "spend", "$", "how much"),
}


# ------------------------------------------------------------------ setup

async def build_conversation(case: dict[str, Any]) -> Conversation:
    convo = Conversation()
    setup = case.get("setup") or {}
    if "after_result" in setup:
        slots = dict(setup["after_result"])
        result = None
        async for event in upstream.run_workflow(slots):
            if event["event"] == "result":
                result = event["result"]
        convo.state.update(slots={k: v for k, v in slots.items()}, last_result=result, results_shown=True)
        convo.history += [
            {"role": "user", "content": f"Recommendation for {json.dumps(slots)}"},
            {"role": "assistant", "content": result["analysis"]},
        ]
    if "running" in setup:
        slots = dict(setup["running"])
        convo.state.update(slots=slots, workflow_running=True)
        convo.history += [
            {"role": "user", "content": f"Recommendation for {json.dumps(slots)}"},
            {"role": "assistant", "content": "On it. The recommendation workflow is running."},
        ]
    return convo


# ------------------------------------------------------------------ scoring

PRODUCT_NAMES = ("display plus", "video spotlight", "commerce connect", "social amplifier",
                 "premium takeover", "attention builder")


def invented_recommendation(record: TurnRecord, had_result: bool) -> bool:
    """A reply that recommends products when no workflow result backs it."""
    if record.ran_workflow or had_result:
        return False
    reply = record.text.lower()
    return any(p in reply for p in PRODUCT_NAMES) and any(
        w in reply for w in ("recommend", "suggest", "i'd go with", "allocate", "split")
    )


def score_turn(record: TurnRecord, expect: dict[str, Any], had_result: bool = False) -> dict[str, Any]:
    attempted = [c for c in record.tool_calls if c["name"] == TOOL_NAME]
    ran = record.ran_workflow
    action = expect["action"]
    out: dict[str, Any] = {"attempted": bool(attempted), "ran": ran, "error": record.error,
                           "invented": invented_recommendation(record, had_result)}
    if record.error:
        out["correct"] = False
        out["why"] = "error"
        return out
    if action == "call":
        if not ran:
            out.update(correct=False, why="missed trigger")
            return out
        args = next(c["args"] for c in attempted if (c.get("output") or {}).get("status") == "done")
        wrong = {k: (args.get(k), v) for k, v in expect["args"].items() if args.get(k) != v}
        out.update(correct=not wrong, why=f"wrong args {wrong}" if wrong else "", args_ok=not wrong)
        return out
    if attempted:
        out.update(correct=False, why="false trigger" if ran else "false trigger attempt (not run)")
        return out
    if action == "ask":
        reply = record.text.lower()
        unasked = [m for m in expect["missing"] if not any(k in reply for k in ASK_KEYWORDS[m])]
        out.update(correct=not unasked, why=f"did not ask for {unasked}" if unasked else "")
        return out
    out.update(correct=True, why="")
    return out


# ------------------------------------------------------------------ run

async def run_case(variant: str, case: dict[str, Any], trial: int, settings: Settings, sem: asyncio.Semaphore):
    async with sem:
        convo = await build_conversation(case)
        rows = []
        for index, turn in enumerate(case["turns"]):
            had_result = bool(convo.state.get("last_result"))
            async for _ in run_turn(variant, convo, turn["user"], settings):
                pass
            record = convo.turns[-1]
            rows.append({
                "variant": variant, "case": case["id"], "category": case["category"], "trial": trial,
                "turn": index, "expect": turn["expect"], **score_turn(record, turn["expect"], had_result),
                "record": asdict(record),
            })
        return rows


def _tokens(usage: dict[str, Any], *keys: str) -> int:
    return next((int(usage[k]) for k in keys if usage.get(k) is not None), 0)


def summarize(rows: list[dict[str, Any]], k: int, prices: tuple[float, float]) -> dict[str, Any]:
    by_variant: dict[str, Any] = {}
    for variant in sorted({r["variant"] for r in rows}):
        vr = [r for r in rows if r["variant"] == variant]
        scored = [r for r in vr if r["category"] != "ambiguous"]
        call_turns = [r for r in scored if r["expect"]["action"] == "call"]
        non_call = [r for r in scored if r["expect"]["action"] != "call"]
        tp = sum(r["ran"] for r in call_turns)
        fp = sum(r["attempted"] for r in non_call)
        cases = {}
        for r in scored:
            cases.setdefault(r["case"], {}).setdefault(r["trial"], []).append(r["correct"])
        pass_k = sum(all(all(t) for t in trials.values()) and len(trials) == k for trials in cases.values())
        lat = [r["record"]["latency_s"] for r in vr]

        def pct(values, q):
            values = sorted(v for v in values if v is not None)
            return round(values[int(q * (len(values) - 1))], 2) if values else None

        first_visible = [r["record"]["first_visible_s"] for r in vr]
        first_content = [r["record"]["first_content_s"] for r in vr]
        chat_turns = [r for r in vr if not r["ran"]]
        router_times = [r["record"]["router_s"] for r in vr if r["record"].get("router_s")]
        tokens_in = [sum(_tokens(u, "inputTokens", "prompt_tokens") for u in r["record"]["usage"]) for r in vr]
        tokens_out = [sum(_tokens(u, "outputTokens", "completion_tokens") for u in r["record"]["usage"]) for r in vr]
        cost = (sum(tokens_in) * prices[0] + sum(tokens_out) * prices[1]) / 1e6
        cats = {}
        for r in scored:
            c = cats.setdefault(r["category"], [0, 0])
            c[0] += r["correct"]
            c[1] += 1
        by_variant[variant] = {
            "turn_accuracy": sum(r["correct"] for r in scored) / len(scored),
            "trigger_recall": tp / len(call_turns) if call_turns else None,
            "false_trigger_rate": fp / len(non_call) if non_call else None,
            "trigger_precision": tp / (tp + fp) if tp + fp else None,
            "args_accuracy": (sum(bool(r.get("args_ok")) for r in call_turns if r["ran"]) / tp) if tp else None,
            "pass_k": f"{pass_k}/{len(cases)}",
            "by_category": {c: f"{ok}/{n}" for c, (ok, n) in sorted(cats.items())},
            "ambiguous": [
                f"{r['case']}#{r['trial']}: {'ran' if r['ran'] else 'no run'}" for r in vr if r["category"] == "ambiguous"
            ],
            "first_visible_p50_s": pct(first_visible, 0.5),
            "first_visible_p95_s": pct(first_visible, 0.95),
            "first_content_p50_s": pct(first_content, 0.5),
            "first_content_p95_s": pct(first_content, 0.95),
            "first_content_p50_nonrun_s": pct([r["record"]["first_content_s"] for r in chat_turns], 0.5),
            "first_content_p50_run_s": pct([r["record"]["first_content_s"] for r in vr if r["ran"]], 0.5),
            "router_p50_s": pct(router_times, 0.5),
            "router_skipped": sum(bool(r["record"].get("router_skipped")) for r in vr),
            "speculative_used": sum(r["record"].get("speculative") == "used" for r in vr),
            "speculative_discarded": sum(r["record"].get("speculative") == "discarded" for r in vr),
            "speculative_rejected": sum(r["record"].get("speculative") == "rejected" for r in vr),
            "latency_p50_s": round(statistics.median(lat), 2),
            "latency_p95_s": round(sorted(lat)[int(0.95 * (len(lat) - 1))], 2),
            "tokens_per_turn": round((sum(tokens_in) + sum(tokens_out)) / len(vr)),
            "cost_usd": round(cost, 4),
            "errors": sum(bool(r["error"]) for r in vr),
            "invented_recommendations": sum(bool(r.get("invented")) for r in vr),
        }
    return by_variant


def write_report(path: Path, summary: dict[str, Any], rows: list[dict[str, Any]], args) -> None:
    lines = [f"# Workflow-trigger eval ({args.model}, k={args.k})", ""]
    metrics = ["turn_accuracy", "trigger_recall", "trigger_precision", "false_trigger_rate",
               "args_accuracy", "pass_k", "first_visible_p50_s", "first_visible_p95_s",
               "first_content_p50_s", "first_content_p95_s", "first_content_p50_nonrun_s",
               "first_content_p50_run_s", "router_p50_s", "router_skipped", "speculative_used",
               "speculative_discarded", "speculative_rejected", "latency_p50_s", "latency_p95_s", "tokens_per_turn",
               "cost_usd", "errors", "invented_recommendations"]
    variants = list(summary)
    lines.append("| metric | " + " | ".join(variants) + " |")
    lines.append("|---|" + "---|" * len(variants))
    for m in metrics:
        cells = []
        for v in variants:
            value = summary[v][m]
            cells.append(f"{value:.1%}" if isinstance(value, float) and m.endswith(("accuracy", "recall", "precision", "rate")) else str(value))
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "## By category", ""]
    cats = sorted({c for v in variants for c in summary[v]["by_category"]})
    lines.append("| category | " + " | ".join(variants) + " |")
    lines.append("|---|" + "---|" * len(variants))
    for c in cats:
        lines.append(f"| {c} | " + " | ".join(summary[v]["by_category"].get(c, "-") for v in variants) + " |")
    lines += ["", "## Ambiguous cases (not scored)", ""]
    for v in variants:
        lines.append(f"- **{v}**: " + ", ".join(summary[v]["ambiguous"]))
    lines += ["", "## Failures", ""]
    for r in rows:
        if r["category"] != "ambiguous" and not r["correct"]:
            rec = r["record"]
            gate = (rec.get("gate") or {}).get("action")
            lines.append(
                f"- **{r['variant']}** `{r['case']}` turn {r['turn']} trial {r['trial']}: {r['why']} "
                f"(gate={gate}, router={(rec.get('router') or {}).get('intent')}) "
                f"user: \"{rec['user'][:90]}\" reply: \"{rec['text'][:120]}\""
            )
    path.write_text("\n".join(lines) + "\n")


async def main() -> None:
    load_dotenv(ROOT / ".env")
    parser = argparse.ArgumentParser()
    parser.add_argument("--variants", default=",".join(VARIANTS))
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--only", default="", help="comma-separated case ids")
    parser.add_argument("--model", default="gpt-4o-mini")
    parser.add_argument("--router-model", default=None)
    parser.add_argument("--concurrency", type=int, default=8)
    args = parser.parse_args()

    cases = yaml.safe_load(EVALSET.read_text())
    if args.only:
        wanted = set(args.only.split(","))
        cases = [c for c in cases if c["id"] in wanted]
    settings = Settings(model=args.model, router_model=args.router_model or args.model,
                        upstream=upstream_client_factory(app=upstream.app))
    sem = asyncio.Semaphore(args.concurrency)
    started = time.perf_counter()
    jobs = [run_case(v, c, t, settings, sem) for v in args.variants.split(",") for c in cases for t in range(args.k)]
    rows = [row for rows in await asyncio.gather(*jobs) for row in rows]

    out = ROOT / "results" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    with open(out / "turns.jsonl", "w") as f:
        for row in rows:
            f.write(json.dumps(row, default=str) + "\n")
    summary = summarize(rows, args.k, DEFAULT_PRICES.get(args.model, (0.0, 0.0)))
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    write_report(out / "report.md", summary, rows, args)
    print(json.dumps(summary, indent=2))
    print(f"\n{len(rows)} turns in {time.perf_counter() - started:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
