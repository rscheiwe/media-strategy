"""Eval (d): the clarification loop, scripted multi-turn, approach A vs B.

    cd app/api && PYTHONPATH=. uv run python -m evals.run_clarify --approach A,B --k 5

Scenarios (each must end as scripted):
  form_submit      brief 6 -> form [vertical] -> submit the form -> done; the submit turn makes
                   no routing/chat model call (only the workflow's explain step)
  text_answer      brief 7 -> form [kpi] -> "Click-through rate." -> done with kpi=ctr
  invalid_geo      LATAM brief -> form [geo] -> "Let's do the US then." -> done with geo=US
  change_mid_loop  brief 6 -> form -> "It's Retail, and make the budget $30k." -> done, both applied
  stale_form       brief 6 -> form -> "It's an Entertainment app." (done) -> submit the old form
                   -> not_run stale_form, nothing re-run
  double_submit    brief 6 -> form -> two submits of the same form at once -> exactly one run
Also counted: re-asks of a known detail (a form asking for a slot the user already gave
validly). Live model calls. Writes results/<ts>/clarify/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from .harness import AGENTS, Harness, TurnRecord, dry_run, results_dir, write_jsonl

BRIEF6 = ("A new mobile app wants efficient awareness in the US with a $25,000 budget and strong "
          "in-view performance, but they have not provided their vertical.")
BRIEF7 = ("A Finance brand wants a recommendation for EMEA with a $25,000 budget, but they have not "
          "said whether they care more about click-through rate or in-view rate.")
LATAM = "Retail client in LATAM, $20k budget, CTR. Recommendation please."
KNOWN = {BRIEF6: {"kpi", "geo", "budget"}, BRIEF7: {"vertical", "geo", "budget"}, LATAM: {"vertical", "kpi", "budget"}}


def submission(form: dict[str, Any], **changes) -> dict[str, Any]:
    params = {f["name"]: f.get("value") for f in form["form_config"]["fields"]}
    params.update(changes)
    return {"type": "tool_form_submission", "target_tool": "run_recommendation", "form_id": form["form_id"], "parameters": params}


class Script:
    def __init__(self, h: Harness, agent: str):
        self.h, self.agent, self.chat, self.history, self.turns = h, agent, None, [], []

    async def say(self, text: str, metadata: dict[str, Any] | None = None) -> TurnRecord:
        self.history = self.history + [{"role": "user", "content": text}]
        rec = await self.h.turn(self.agent, self.history, chat_id=self.chat, metadata=metadata)
        self.chat = rec.chat_id or self.chat
        self.history = self.history + [{"role": "assistant", "content": rec.text}]
        self.turns.append(rec)
        return rec


def reasks(first: TurnRecord, brief: str) -> list[str]:
    return sorted(set((first.form or {}).get("missing") or []) & KNOWN[brief])


def non_explain_calls(rec: TurnRecord) -> int:
    return sum(1 for c in rec.calls if c["label"] != "explain")


async def form_submit(s: Script) -> dict[str, Any]:
    first = await s.say(BRIEF6)
    if not first.form:
        return {"ok": False, "why": f"no form (ran={first.ran})", "reasks": []}
    sub = await s.say("Vertical: Entertainment", submission(first.form, vertical="Entertainment"))
    ok = sub.ran and sub.slots_after.get("vertical") == "Entertainment" and non_explain_calls(sub) == 0
    return {"ok": ok, "why": "" if ok else f"ran={sub.ran} non_explain_calls={non_explain_calls(sub)}",
            "reasks": reasks(first, BRIEF6), "submit_llm_calls": non_explain_calls(sub)}


async def text_answer(s: Script) -> dict[str, Any]:
    first = await s.say(BRIEF7)
    if not first.form:
        return {"ok": False, "why": f"no form (ran={first.ran})", "reasks": []}
    second = await s.say("Click-through rate.")
    ok = second.ran and second.slots_after.get("kpi") == "ctr" and second.slots_after.get("vertical") == "Finance"
    return {"ok": ok, "why": "" if ok else f"ran={second.ran} form={(second.form or {}).get('missing')} slots={second.slots_after}",
            "reasks": reasks(first, BRIEF7)}


async def invalid_geo(s: Script) -> dict[str, Any]:
    first = await s.say(LATAM)
    if not first.form or "geo" not in first.form["missing"]:
        return {"ok": False, "why": f"no geo form (ran={first.ran}, form={(first.form or {}).get('missing')})", "reasks": []}
    second = await s.say("Let's do the US then.")
    ok = second.ran and second.slots_after.get("geo") == "US"
    return {"ok": ok, "why": "" if ok else f"ran={second.ran} slots={second.slots_after}", "reasks": reasks(first, LATAM)}


async def change_mid_loop(s: Script) -> dict[str, Any]:
    first = await s.say(BRIEF6)
    if not first.form:
        return {"ok": False, "why": "no form", "reasks": []}
    second = await s.say("It's Retail, and make the budget $30k.")
    ok = second.ran and second.slots_after.get("vertical") == "Retail" and second.slots_after.get("budget") == 30000
    return {"ok": ok, "why": "" if ok else f"ran={second.ran} slots={second.slots_after}", "reasks": reasks(first, BRIEF6)}


async def stale_form(s: Script) -> dict[str, Any]:
    first = await s.say(BRIEF6)
    if not first.form:
        return {"ok": False, "why": "no form", "reasks": []}
    second = await s.say("It's an Entertainment app.")
    if not second.ran:
        return {"ok": False, "why": f"text answer didn't run (form={(second.form or {}).get('missing')})", "reasks": reasks(first, BRIEF6)}
    third = await s.say("Vertical: QSR", submission(first.form, vertical="QSR"))
    ok = not third.ran and third.not_run == "stale_form" and s.h.slots(s.chat).get("vertical") == "Entertainment"
    return {"ok": ok, "why": "" if ok else f"ran={third.ran} not_run={third.not_run}", "reasks": reasks(first, BRIEF6)}


async def double_submit(s: Script) -> dict[str, Any]:
    first = await s.say(BRIEF6)
    if not first.form:
        return {"ok": False, "why": "no form", "reasks": []}
    meta = submission(first.form, vertical="Entertainment")
    history = s.history + [{"role": "user", "content": "Vertical: Entertainment"}]
    a, b = await asyncio.gather(s.h.turn(s.agent, history, chat_id=s.chat, metadata=meta),
                                s.h.turn(s.agent, history, chat_id=s.chat, metadata=meta))
    s.turns += [a, b]
    runs = sum(r.ran for r in (a, b))
    ok = runs == 1 and {r.not_run for r in (a, b) if not r.ran} <= {"stale_form", "already_running"}
    return {"ok": ok, "why": "" if ok else f"runs={runs} not_run={[a.not_run, b.not_run]}", "reasks": reasks(first, BRIEF6)}


SCENARIOS = {f.__name__: f for f in (form_submit, text_answer, invalid_geo, change_mid_loop, stale_form, double_submit)}


async def run_one(h: Harness, approach: str, name: str, trial: int, sem: asyncio.Semaphore) -> dict[str, Any]:
    async with sem:
        s = Script(h, AGENTS[approach])
        try:
            result = await SCENARIOS[name](s)
        except Exception as exc:  # noqa: BLE001 - a crash is a failed scenario
            result = {"ok": False, "why": f"{type(exc).__name__}: {exc}", "reasks": []}
        return {"approach": approach, "scenario": name, "trial": trial, **result,
                "turns": [t.summary() for t in s.turns]}


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    out = {}
    for approach in sorted({r["approach"] for r in rows}):
        ar = [r for r in rows if r["approach"] == approach]
        out[approach] = {
            "completion": round(sum(r["ok"] for r in ar) / len(ar), 4),
            "by_scenario": {n: f"{sum(r['ok'] for r in ar if r['scenario'] == n)}/{sum(r['scenario'] == n for r in ar)}" for n in SCENARIOS},
            "reasks_of_known_details": sum(len(r["reasks"]) for r in ar),
            "submit_turn_llm_calls": sum(r.get("submit_llm_calls", 0) for r in ar),
            "failures": [f"{r['scenario']}#{r['trial']}: {r['why']}" for r in ar if not r["ok"]],
        }
    return out


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approach", default="A,B")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="validate inputs, write the job plan, no model calls")
    args = parser.parse_args()
    names = args.only.split(",") if args.only else list(SCENARIOS)
    if args.dry_run:
        assert all(n in SCENARIOS for n in names), names
        dry_run("clarify", [{"approach": a, "scenario": n, "k": args.k} for a in args.approach.split(",") for n in names])
        return
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    async with Harness() as h:
        for approach in args.approach.split(","):
            sem = asyncio.Semaphore(args.concurrency)
            rows += await asyncio.gather(*[run_one(h, approach, n, t, sem) for n in names for t in range(args.k)])
    out = results_dir("clarify")
    write_jsonl(out / "scenarios.jsonl", rows)
    summary = summarize(rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    lines = [f"# Clarification-loop eval, k={args.k}", "", "Targets: 100% completion; 0 re-asks of known details; 0 routing/chat model calls on a submit turn.", ""]
    for a, s in summary.items():
        lines += [f"## {a}", "", f"- completion: {s['completion']:.1%}", f"- by scenario: {s['by_scenario']}",
                  f"- re-asks of known details: {s['reasks_of_known_details']}", f"- submit-turn model calls (excl. explain): {s['submit_turn_llm_calls']}",
                  "- failures:" + ("" if s["failures"] else " none")] + [f"  - {f}" for f in s["failures"]] + [""]
    (out / "report.md").write_text("\n".join(lines))
    print(json.dumps(summary, indent=2))
    print(f"\n{len(rows)} scenarios in {time.perf_counter() - started:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
