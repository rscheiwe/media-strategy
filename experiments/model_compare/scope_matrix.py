"""Scope checks for case-data questions: does the agent keep the right scope, flag what the
data can't do (geo on campaign history), and NOT invent a problem where there is none?

    cd app/api && PYTHONPATH=../../experiments/model_compare \\
        uv run python ../../experiments/model_compare/scope_matrix.py [--runs 3]
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
from collections import defaultdict
from pathlib import Path

from compare import make_agent

ROOT = Path(__file__).resolve().parents[2]
ROWS = list(csv.DictReader((ROOT / "app" / "api" / "case_data" / "campaign_history.csv").open()))


def avg_ctr(vertical: str | None) -> set[str]:
    """Both defensible readings of "average CTR", as 2-decimal percentages: the mean of
    the per-product CTRs, and the pooled ratio of sums (all clicks / all impressions)."""
    totals = defaultdict(lambda: [0, 0])
    for row in ROWS:
        if vertical is None or row["vertical"] == vertical:
            totals[row["product_id"]][0] += int(row["impressions"])
            totals[row["product_id"]][1] += int(row["clicks"])
    mean = sum(c / i for i, c in totals.values()) / len(totals)
    pooled = sum(c for _, c in totals.values()) / sum(i for i, _ in totals.values())
    return {f"{mean:.2%}", f"{pooled:.2%}"}


QSR_TURN = [
    {"role": "user", "content": "If a QSR brand had $15k for the US, would you lean toward video?"},
    {"role": "assistant", "content": "I wouldn't lean toward video on these numbers: for QSR in the US, Video Spotlight has a 0.60% CTR, the lowest of the six."},
]
ASK_US = {"role": "user", "content": "Find the average CTR of the US products"}

# (label, messages, expected vertical filter on campaign_history, expected CTR, geo note expected?)
CASES = [
    ("A follow-up after QSR: 'US products'", QSR_TURN + [ASK_US], "QSR", avg_ctr("QSR"), True),
    ("B fresh: 'US products'", [ASK_US], None, avg_ctr(None), True),
    ("C fresh: Retail (no geo in question)", [{"role": "user", "content": "What's the average CTR for Retail products?"}], "Retail", avg_ctr("Retail"), False),
    ("D fresh: QSR US inventory", [{"role": "user", "content": "Which QSR products have the most US inventory?"}], None, None, False),
    ("E the QSR video question", [QSR_TURN[0]], "QSR", None, False),
]

GEO_NOTE = re.compile(
    r"(isn.t|not|no|aren.t|doesn.t|don.t have)\b[^.]{0,50}\b(geo|region|US-specific|US-only|by geography|by region)"
    r"|geo-specific|no geo|geo split|split by geo|geo field|geo breakdown",
    re.I,
)
CANT = re.compile(r"\b(can.t|cannot|unable|not possible)\b", re.I)


def history_scope(fetched: list) -> str | None:
    """The vertical(s) the final campaign_history fetch used, as a comparable string."""
    calls = [f for d, f in fetched if d == "campaign_history" and not (f or {}).get("geo")]
    if not calls:
        return "none"
    vertical = (calls[-1] or {}).get("vertical")
    if vertical is None or (isinstance(vertical, list) and len(vertical) == 5):
        return None  # all verticals
    return vertical if isinstance(vertical, str) else ",".join(vertical)


async def run(messages: list) -> tuple:
    agent = make_agent("openai", "gpt-5.4-mini", None)
    fetched, text = [], []
    async for event in agent.run_stream({"messages": messages}, stateless=True):
        if not isinstance(event, dict):
            continue
        if event.get("type") == "tool-input-available" and event["toolName"] == "get_case_data":
            inp = event.get("input") or {}
            fetched.append((inp.get("dataset"), inp.get("filters")))
        if event.get("type") == "text-delta":
            text.append(event.get("delta") or "")
    return fetched, "".join(text).strip().replace("\n", " ")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=3)
    args = parser.parse_args()
    for label, messages, scope, ctr, wants_note in CASES:
        print(f"\n### {label}")
        passed = 0
        for _ in range(args.runs):
            fetched, answer = await run(messages)
            geo_attempts = sum(1 for d, f in fetched if d == "campaign_history" and (f or {}).get("geo"))
            note = bool(GEO_NOTE.search(answer))
            nums = re.findall(r"\d+\.\d+%", answer)
            checks = {"geo_note" if wants_note else "no_false_geo_note": note == wants_note}
            if ctr is not None:
                checks["ctr"] = bool(ctr & set(nums))
            if label.startswith(("A", "B", "C")):
                checks["scope"] = history_scope(fetched) == scope
            if label.startswith("D"):
                checks["inventory"] = ("inventory", {"vertical": "QSR", "geo": "US"}) in fetched and bool(
                    re.search(r"Social Amplifier|P004", answer)
                )
            if not wants_note:
                checks["no_cant"] = not CANT.search(answer)
            ok = all(checks.values())
            passed += ok
            print(f"  {'PASS' if ok else 'FAIL'} {checks} failed_geo_calls={geo_attempts} | {answer[:150]}")
        print(f"  -> {passed}/{args.runs}")


if __name__ == "__main__":
    asyncio.run(main())
