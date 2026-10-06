"""Eval (b): slot extraction, approach A vs B, on one-message briefs.

    cd app/api && PYTHONPATH=. uv run python -m evals.run_extraction --approach A,B --k 5

Scores the run_recommendation input each approach produced (A: the agent's tool call;
B: the router's extracted details, dispatched) against evals/data/extraction.yaml:
  - per-slot accuracy (vertical, kpi, geo, budget, impression_goal);
  - slot hallucinations: a vertical/KPI where the brief gave none;
  - null misses: a stated detail left null;
and, "with the grounding check", how many hallucinations still reached a recommendation
(versus being caught and sent to the form as "please confirm"). Items marked ambiguous are
reported, not scored. Live model calls. Writes results/<ts>/extraction/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import yaml

from .harness import AGENTS, Harness, dry_run, results_dir, write_jsonl

DATA = Path(__file__).resolve().parent / "data" / "extraction.yaml"
SLOTS = ("vertical", "kpi", "geo", "budget", "impression_goal")


def _norm(value: Any) -> Any:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return value


def score(item: dict[str, Any], args: dict[str, Any] | None, form_missing: list[str] | None, ran: bool) -> dict[str, Any]:
    expect = item["expect"]
    if args is None:
        return {"extracted": False, "slots": {}, "hallucinated": [], "null_misses": [], "reached_rec": []}
    slots = {s: _norm(args.get(s)) == _norm(expect.get(s)) for s in SLOTS}
    hallucinated = [s for s in ("vertical", "kpi") if expect.get(s) is None and args.get(s) is not None]
    null_misses = [s for s in SLOTS if expect.get(s) is not None and args.get(s) is None]
    # A hallucinated value is caught if the run stopped at the form asking about that slot.
    reached = [s for s in hallucinated if ran or s not in (form_missing or [])]
    return {"extracted": True, "slots": slots, "hallucinated": hallucinated, "null_misses": null_misses, "reached_rec": reached}


async def run_item(h: Harness, approach: str, item: dict[str, Any], trial: int, sem: asyncio.Semaphore):
    async with sem:
        rec = await h.turn(AGENTS[approach], [{"role": "user", "content": item["text"]}])
        call = next((c for c in rec.tool_calls if c["name"] == "run_recommendation"), None)
        args = call["input"] if call else None
        form = rec.form
        return {"approach": approach, "item": item["id"], "trial": trial, "ambiguous": bool(item.get("ambiguous")),
                "expect": item["expect"], "args": args, "ran": rec.ran, "form_missing": (form or {}).get("missing"),
                "error": rec.error, **score(item, args, (form or {}).get("missing"), rec.ran)}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for approach in sorted({r["approach"] for r in rows}):
        scored = [r for r in rows if r["approach"] == approach and not r["ambiguous"]]
        extracted = [r for r in scored if r["extracted"]]
        n_null = sum(1 for r in extracted for s in SLOTS if r["expect"].get(s) is not None)
        out[approach] = {
            "items": len(scored),
            "no_extraction": len(scored) - len(extracted),
            "per_slot_accuracy": {s: round(sum(r["slots"][s] for r in extracted) / len(extracted), 4) if extracted else None for s in SLOTS},
            "all_slots_exact": round(sum(all(r["slots"].values()) for r in extracted) / len(extracted), 4) if extracted else None,
            "slot_hallucinations": sum(len(r["hallucinated"]) for r in scored),
            "hallucinations_reaching_recommendation": sum(len(r["reached_rec"]) for r in scored),
            "null_miss_rate": round(sum(len(r["null_misses"]) for r in extracted) / n_null, 4) if n_null else None,
            "errors": sum(bool(r["error"]) for r in scored),
            "hallucinated_items": sorted({f"{r['item']}:{','.join(r['hallucinated'])}" for r in scored if r["hallucinated"]}),
            "ambiguous": [f"{r['item']}#{r['trial']}: {json.dumps({s: (r['args'] or {}).get(s) for s in SLOTS})}"
                          for r in rows if r["approach"] == approach and r["ambiguous"]],
        }
    return out


def report(summary: dict[str, Any], rows: list[dict[str, Any]], k: int) -> str:
    approaches = list(summary)
    lines = [f"# Slot extraction eval: A vs B, k={k}", "",
             "Targets: per-slot ≥ 98%, **0 hallucinated vertical/KPI**, null misses ≤ 2%.", "",
             "| metric | " + " | ".join(approaches) + " |", "|---|" + "---|" * len(approaches)]
    for m in ("items", "no_extraction", "all_slots_exact", "slot_hallucinations",
              "hallucinations_reaching_recommendation", "null_miss_rate", "errors"):
        lines.append(f"| {m} | " + " | ".join(str(summary[a][m]) for a in approaches) + " |")
    for s in SLOTS:
        lines.append(f"| accuracy: {s} | " + " | ".join(str(summary[a]["per_slot_accuracy"][s]) for a in approaches) + " |")
    lines += ["", "## Errors by item", ""]
    for r in rows:
        if r["ambiguous"]:
            continue
        bad = [s for s, ok in r["slots"].items() if not ok] if r["extracted"] else ["(no extraction)"]
        if bad:
            got = {s: (r["args"] or {}).get(s) for s in bad} if r["extracted"] else {}
            lines.append(f"- **{r['approach']}** `{r['item']}` #{r['trial']}: {bad} got {got}")
    lines += ["", "## Ambiguous items (not scored)", ""] + [f"- **{a}**: " + "; ".join(summary[a]["ambiguous"]) for a in approaches]
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approach", default="A,B")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="validate inputs, write the job plan, no model calls")
    args = parser.parse_args()
    items = yaml.safe_load(DATA.read_text())
    if args.only:
        items = [i for i in items if i["id"] in set(args.only.split(","))]
    if args.dry_run:
        for i in items:
            assert set(i["expect"]) == set(SLOTS), i["id"]
        dry_run("extraction", [{"approach": a, "item": i["id"], "k": args.k} for a in args.approach.split(",") for i in items])
        return
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    async with Harness() as h:
        for approach in args.approach.split(","):
            sem = asyncio.Semaphore(args.concurrency)
            rows += await asyncio.gather(*[run_item(h, approach, i, t, sem) for i in items for t in range(args.k)])
    out = results_dir("extraction")
    write_jsonl(out / "items.jsonl", rows)
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.md").write_text(report(summary, rows, args.k))
    print(json.dumps(summary, indent=2))
    print(f"\n{len(rows)} items in {time.perf_counter() - started:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
