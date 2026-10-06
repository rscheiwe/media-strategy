"""Prompt ablation: does each behaviour rule earn its place?

Runs the real chat agent (gpt-5.4-mini, the app default) with each prompt variant on a
broad case set, N runs each, and grades every answer against truth computed from the raw
CSVs. Variants: see variants.py.

    cd app/api
    PYTHONPATH=../../experiments/prompt_ablation:../../experiments/model_compare \\
        uv run python ../../experiments/prompt_ablation/ablation.py [--runs 5] [--only V1]

Cases:
- the QSR video question (graded by a judge given the full truth);
- the 5 scope cases (deterministic checks, from model_compare/scope_matrix.py);
- the 7 sample client briefs (graded by a judge given the brief's vertical x geo truth).
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import dataclasses
import json
import os
import re
import statistics
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import httpx
from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
load_dotenv(ROOT / "app" / ".env")

import chat_api.chat.agent as agent_module  # noqa: E402
from scope_matrix import CANT, GEO_NOTE, QSR_TURN, ASK_US, avg_ctr, history_scope  # noqa: E402
from variants import PROMPTS, tools_for  # noqa: E402

MODEL = "gpt-5.4-mini"
JUDGE = "gpt-5.4"

# --- truth from the raw CSVs -----------------------------------------------------------
HISTORY = list(csv.DictReader((ROOT / "app" / "api" / "case_data" / "campaign_history.csv").open()))
CATALOG = {r["product_id"]: r for r in csv.DictReader((ROOT / "app" / "api" / "case_data" / "product_catalog.csv").open())}
INVENTORY = list(csv.DictReader((ROOT / "app" / "api" / "case_data" / "inventory_forecaster.csv").open()))
BRIEFS = json.loads((ROOT / "app" / "api" / "case_data" / "client_briefs.json").read_text())


def truth_table(vertical: str | None, geo: str | None, budget: float) -> str:
    """Every figure a correct answer might state, from the raw CSVs, unrounded enough to
    check derived numbers (expected clicks, eCPC, baselines elsewhere, line-item ranges)."""
    totals, other, items = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0, 0]), defaultdict(list)
    for r in HISTORY:
        target = totals if (vertical is None or r["vertical"] == vertical) else other
        x = target[r["product_id"]]
        x[0] += int(r["impressions"]); x[1] += int(r["clicks"]); x[2] += int(r["viewable_impressions"])
        if vertical is None or r["vertical"] == vertical:
            items[r["product_id"]].append(int(r["viewable_impressions"]) / int(r["impressions"]))
    inv = defaultdict(lambda: [0, []])
    for r in INVENTORY:
        if (vertical is None or r["vertical"] == vertical) and (geo is None or r["geo"] == geo):
            inv[r["product_id"]][0] += int(r["available_imps"]); inv[r["product_id"]][1].append(float(r["inventory_risk"]))
    lines = [f"Scope: vertical={vertical or 'all'}, geo={geo or 'all'}, budget=${budget:,.0f}. Rates are ratio-of-sums over line items."]
    for pid in sorted(totals, key=lambda p: -totals[p][1] / totals[p][0]):
        i, c, v = totals[pid]; ctr, ivr = c / i, v / i
        cpm = float(CATALOG[pid]["cpm"]); bought = budget / cpm * 1000
        avail, risks = inv[pid]
        line = (
            f"- {CATALOG[pid]['product_name']} ({pid}): CPM ${cpm:.0f}, CTR {ctr:.4%}, in-view {ivr:.4%}, "
            f"eCPC ${cpm / (1000 * ctr):.4f}, vCPM ${cpm / ivr:.2f}; budget buys {bought:,.0f} imps -> "
            f"{bought * ctr:,.0f} clicks, {bought * ivr:,.0f} viewable; forecast available {avail:,} "
            f"(coverage {avail / bought:.2%}, confidence {statistics.mean(risks) if risks else 0:.2f}); "
            f"{len(items[pid])} line items, per-line in-view {min(items[pid]):.2%}-{max(items[pid]):.2%}"
        )
        if vertical is not None:
            oi, oc, ov = other[pid]
            line += f"; in other verticals CTR {oc / oi:.2%}, in-view {ov / oi:.2%}"
        lines.append(line)
    return "\n".join(lines)


def brief_scope(text: str) -> tuple[str | None, str | None, float]:
    vertical = next((v for v in ["Retail", "QSR", "Finance", "Entertainment", "Travel"] if v in text), None)
    geo = next((g for g in ["US", "EMEA", "APAC"] if re.search(rf"\b{g}\b", text)), None)
    budget = float(re.search(r"\$([\d,]+)", text).group(1).replace(",", ""))
    return vertical, geo, budget


# --- cases ------------------------------------------------------------------------------
QSR_Q = "If a QSR brand had $15k for the US, would you lean toward video?"
QSR_FACTS = (
    "Correct reading: Social Amplifier leads QSR on BOTH CTR (1.15%) and in-view (78.72%), but $15k buys "
    "1,666,667 imps vs 1,661,000 US forecast (99.7% coverage) at the lowest confidence (0.78), so it may not "
    "absorb the whole budget. Attention Builder beats Video Spotlight on EVERY metric (CTR 0.94% vs 0.60%, "
    "in-view 74.96% vs 74.84%, CPM $14 vs $18) and fits. So video is not the best choice on either KPI; "
    "an answer leaning toward video (even 'for in-view') is incorrect."
)

CASES = [{"id": "qsr", "kind": "judge", "messages": [{"role": "user", "content": QSR_Q}],
          "truth": truth_table("QSR", "US", 15000) + "\n" + QSR_FACTS}]
for label, messages, scope, ctr, wants_note in [
    ("scope-A", QSR_TURN + [ASK_US], "QSR", avg_ctr("QSR"), True),
    ("scope-B", [ASK_US], None, avg_ctr(None), True),
    ("scope-C", [{"role": "user", "content": "What's the average CTR for Retail products?"}], "Retail", avg_ctr("Retail"), False),
    ("scope-D", [{"role": "user", "content": "Which QSR products have the most US inventory?"}], None, None, False),
]:
    CASES.append({"id": label, "kind": "scope", "messages": messages, "scope": scope, "ctr": ctr, "note": wants_note})
for n, text in enumerate(BRIEFS, start=1):
    vertical, geo, budget = brief_scope(text)
    CASES.append({"id": f"brief-{n}", "kind": "judge", "messages": [{"role": "user", "content": text}],
                  "truth": truth_table(vertical, geo, budget)})

JUDGE_PROMPT = """Grade a media-planning assistant's answer against the true data. Be strict about facts, lenient about style.

User message(s):
{question}

True data:
{truth}
Rounding: figures within about 1% of the true value, or rounded versions of it, are correct. Derived figures that
follow from the true data (expected clicks = impressions x CTR, eCPC, vCPM, coverage, baselines in other verticals,
line-item ranges) are correct when consistent with it, even if not listed verbatim.
Field note: the dataset column inventory_risk is a forecast CONFIDENCE score (0-1, higher = more reliable); the
"confidence" figures above are that column. An answer quoting it under the name "inventory risk" has the right
number (don't fail numbers_correct for the name), though calling it risk is a clarity problem worth a note.

Answer:
<<<
{answer}
>>>

Return JSON only:
{{"numbers_correct": bool,      // every figure stated is consistent with the true data (rounding ok)
 "kpi_reading_correct": bool,   // for the KPI(s) discussed, it names the right leader / ranking, and never calls a product best on a KPI it isn't
 "handles_missing_info": bool,  // if the request lacks the vertical or the KPI, it says so / asks; true if nothing is missing
 "addresses_viability": bool,   // mentions inventory coverage/confidence or the impression goal when they matter; true if irrelevant
 "conclusion_correct": bool,    // the overall answer is supported by the data
 "notes": str}}"""


async def judge(case: dict, answer: str) -> dict:
    question = "\n".join(f"{m['role']}: {m['content']}" for m in case["messages"])
    async with httpx.AsyncClient(timeout=180) as client:
        r = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={"model": JUDGE, "reasoning_effort": "low", "response_format": {"type": "json_object"},
                  "messages": [{"role": "user", "content": JUDGE_PROMPT.format(question=question, truth=case["truth"], answer=answer)}]},
        )
    r.raise_for_status()
    return json.loads(r.json()["choices"][0]["message"]["content"])


# --- running ----------------------------------------------------------------------------
def make_agent(variant: str):
    """The app's own agent, with the variant's system prompt and tools swapped in."""
    saved = (agent_module.agent_tools, agent_module.get_chat_agent_config,
             os.environ.get("APP_CHAT_PROVIDER"), os.environ.get("APP_CHAT_MODEL"))
    base = agent_module.get_chat_agent_config
    agent_module.agent_tools = lambda: tools_for(variant)
    agent_module.get_chat_agent_config = lambda uuid=None: dataclasses.replace(base(uuid), system_prompt=PROMPTS[variant])
    os.environ["APP_CHAT_PROVIDER"], os.environ["APP_CHAT_MODEL"] = "openai", MODEL
    try:
        return agent_module.create_chat_agent(api_key=os.getenv("OPENAI_API_KEY"))
    finally:
        agent_module.agent_tools, agent_module.get_chat_agent_config = saved[0], saved[1]
        for key, value in (("APP_CHAT_PROVIDER", saved[2]), ("APP_CHAT_MODEL", saved[3])):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


async def run_case(variant: str, case: dict) -> dict:
    agent = make_agent(variant)
    started = time.monotonic()
    fetched, text, tools, usage = [], [], 0, defaultdict(int)
    try:
        async for e in agent.run_stream({"messages": case["messages"]}, stateless=True):
            if not isinstance(e, dict):
                continue
            if e.get("type") == "tool-input-available":
                tools += 1
                if e["toolName"] == "get_case_data":
                    i = e.get("input") or {}
                    fetched.append((i.get("dataset"), i.get("filters")))
            elif e.get("type") == "text-delta":
                text.append(e.get("delta") or "")
            elif e.get("type") == "response-metadata":
                for k, v in (e.get("usage") or {}).items():
                    if isinstance(v, (int, float)):
                        usage[k] += v
    except Exception as exc:  # noqa: BLE001
        text.append(f"[run error: {exc}]")
    answer = "".join(text).strip()
    return await grade_only(case, {"variant": variant, "case": case["id"], "answer": answer, "fetched": fetched, "tools": tools,
              "seconds": round(time.monotonic() - started, 2), "words": len(answer.split()), "usage": dict(usage)})


async def grade_only(case: dict, result: dict) -> dict:
    answer, fetched = result["answer"], [tuple(f) for f in result["fetched"]]
    if case["kind"] == "scope":
        flat = answer.replace("\n", " ")
        checks = {"geo_note_right": bool(GEO_NOTE.search(flat)) == case["note"]}
        if case["ctr"]:
            # Accept either reading (mean of products / pooled), at any precision the answer uses.
            truths = [float(t.rstrip("%")) for t in case["ctr"]]
            stated = [float(x) for x in re.findall(r"(\d+(?:\.\d+)?)%", flat)]
            checks["ctr"] = any(abs(x - t) <= 0.006 or (round(t, 1) == x) for x in stated for t in truths)
        if case["id"] in ("scope-A", "scope-B", "scope-C"):
            checks["scope"] = history_scope(fetched) == case["scope"]
        if case["id"] == "scope-D":
            checks["inventory"] = ("inventory", {"vertical": "QSR", "geo": "US"}) in fetched and bool(re.search(r"Social Amplifier|P004", flat))
        if not case["note"]:
            checks["no_cant"] = not CANT.search(flat)
        result["grade"] = checks
    else:
        result["grade"] = await judge(case, answer) if answer else {"conclusion_correct": False, "notes": "empty"}
    return result


def passed(result: dict) -> bool:
    g = result["grade"]
    return all(v for k, v in g.items() if isinstance(v, bool))


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--regrade", default="", help="results dir: re-grade its answers instead of re-running")
    args = parser.parse_args()
    variants = [v for v in PROMPTS if not args.only or v in args.only.split(",")]
    out_dir = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)
    gate = asyncio.Semaphore(args.concurrency)

    async def one(variant, case, n):
        async with gate:
            r = await run_case(variant, case)
            r["run"] = n
            print(f"{variant} {case['id']:9} run {n}: {'PASS' if passed(r) else 'FAIL'} {r['seconds']:5.1f}s {r['words']:4}w", flush=True)
            return r

    if args.regrade:
        by_id = {c["id"]: c for c in CASES}
        old = json.loads((Path(args.regrade) / "runs.json").read_text())

        async def regrade(r):
            async with gate:
                fresh = dict(r)
                fresh["grade"] = (await grade_only(by_id[r["case"]], r))["grade"]
                return fresh

        results = await asyncio.gather(*(regrade(r) for r in old if r["variant"] in variants))
    else:
        results = await asyncio.gather(*(one(v, c, n) for v in variants for c in CASES for n in range(1, args.runs + 1)))
    (out_dir / "runs.json").write_text(json.dumps(results, indent=2, default=str))

    print(f"\n{'variant':8} {'pass all':>9} {'qsr':>6} {'scope':>7} {'briefs':>7}   per-criterion (judge cases)                       words p50  secs p50  in-tokens")
    for v in variants:
        rs = [r for r in results if r["variant"] == v]
        rate = lambda sel: f"{sum(passed(r) for r in sel)}/{len(sel)}"  # noqa: E731
        judged = [r for r in rs if not r["case"].startswith("scope")]
        crit = {k: sum(bool(r["grade"].get(k)) for r in judged) for k in
                ("numbers_correct", "kpi_reading_correct", "handles_missing_info", "addresses_viability", "conclusion_correct")}
        print(f"{v:8} {rate(rs):>9} {rate([r for r in rs if r['case'] == 'qsr']):>6} "
              f"{rate([r for r in rs if r['case'].startswith('scope')]):>7} {rate([r for r in rs if r['case'].startswith('brief')]):>7}   "
              + " ".join(f"{k.split('_')[0]}={n}/{len(judged)}" for k, n in crit.items())
              + f"   {statistics.median(r['words'] for r in rs):6.0f}  {statistics.median(r['seconds'] for r in rs):7.1f}  "
              f"{statistics.mean(r['usage'].get('inputTokens', 0) for r in rs):9.0f}")
    print(f"\nresults: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
