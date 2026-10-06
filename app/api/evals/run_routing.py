"""Eval (c): turn routing, approach A (agent calls the tool) vs B (router + gate + dispatch).

    cd app/api && PYTHONPATH=. uv run python -m evals.run_routing --approach A,B --k 5
    PYTHONPATH=. uv run python -m evals.run_routing --only brief1_acme,greeting --k 1   # smoke

Runs every conversation in evals/data/routing.yaml through the real FastAPI stack (live
model calls; needs OPENAI_API_KEY in app/.env). Per user turn, expect.action is:
  call    - the workflow ran (status done) on these details (impression_goal None unless given)
  ask     - the workflow returned FORM_REQUIRED for exactly these missing details
  no_call - the workflow didn't run or ask, and the user saw a reply
Category "ambiguous" is reported but not scored. Writes results/<ts>/routing/.

Decision rule (fixed before the run, see the plan): default to A if, at k=5, A is within 1
point of B on turn accuracy with 0 false triggers, 0 invented recommendations and 0 slot
hallucinations; otherwise B.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from pathlib import Path
from typing import Any

import yaml

from .harness import AGENTS, Harness, TurnRecord, dry_run, invented_recommendation, pct, results_dir, write_jsonl

DATA = Path(__file__).resolve().parent / "data" / "routing.yaml"
SLOTS = ("vertical", "kpi", "geo", "budget", "impression_goal")


def brief_line(slots: dict[str, Any]) -> str:
    goal = f", at least {slots['impression_goal']:,} impressions" if slots.get("impression_goal") else ""
    kpi = "CTR" if slots["kpi"] == "ctr" else "in-view rate"
    who = slots.get("client_name") or f"A {slots['vertical']} client"
    return f"{who} is a {slots['vertical']} advertiser in {slots['geo']} with a ${slots['budget']:,} budget, optimizing {kpi}{goal}."


async def setup(h: Harness, agent: str, case: dict[str, Any]) -> tuple[str | None, list[dict[str, str]], bool]:
    """The chat, prior messages and whether a result was already shown."""
    s = case.get("setup") or {}
    if "after_result" in s:
        chat = h.new_chat(agent)
        out = await h.seed_result(chat, s["after_result"])
        return chat, [{"role": "user", "content": brief_line(s["after_result"])},
                      {"role": "assistant", "content": out["summary"]["headline"] + "."}], True
    if "running" in s:
        chat = h.new_chat(agent)
        h.seed_running(chat, s["running"])
        return chat, [{"role": "user", "content": brief_line(s["running"])},
                      {"role": "assistant", "content": "Running the recommendation workflow…"}], False
    return None, [], False


def hallucinated(rec: TurnRecord, case_text: str) -> list[str]:
    """Vertical/KPI the workflow ran on that the user never stated (ran turns only)."""
    from chat_api.chat.workflow_tool import SUPPORT
    import re

    out = []
    if not rec.ran:
        return out
    for name in ("vertical", "kpi"):
        value = rec.slots_after.get(name)
        pattern = SUPPORT[name].get(value)
        if value and pattern and not re.search(pattern, case_text.lower()):
            out.append(f"{name}={value}")
    return out


def score(rec: TurnRecord, expect: dict[str, Any], had_result: bool, user_text_so_far: str) -> dict[str, Any]:
    out: dict[str, Any] = {"attempted": rec.attempted, "ran": rec.ran, "formed": rec.form is not None,
                           "not_run": rec.not_run, "visible": rec.visible, "error": rec.error,
                           "invented": invented_recommendation(rec, had_result),
                           "hallucinated": hallucinated(rec, user_text_so_far)}
    action = expect["action"]
    if rec.error:
        return {**out, "correct": False, "why": f"error: {rec.error[:120]}"}
    if action == "call":
        if not rec.ran:
            form = rec.form
            why = f"asked instead ({form['missing']})" if form else "missed trigger"
            return {**out, "correct": False, "why": why}
        want = {k: expect["args"].get(k) for k in SLOTS}
        got = {k: rec.slots_after.get(k) for k in SLOTS}
        wrong = {k: (got[k], want[k]) for k in SLOTS if got[k] != want[k]}  # 30000.0 == 30000
        return {**out, "correct": not wrong, "args_ok": not wrong, "why": f"wrong args {wrong}" if wrong else ""}
    if action == "ask":
        form = rec.form
        if rec.ran:
            return {**out, "correct": False, "why": "ran without asking"}
        if not form:
            return {**out, "correct": False, "why": "no form" + (" (asked in text)" if rec.text.strip() else "")}
        ok = set(form["missing"]) == set(expect["missing"])
        return {**out, "correct": ok, "why": "" if ok else f"form missing {form['missing']} != {expect['missing']}"}
    # no_call
    if rec.ran or rec.form:
        return {**out, "correct": False, "why": "false trigger" if rec.ran else "false ask (form)"}
    if not rec.visible:
        return {**out, "correct": False, "why": f"silent turn (tool not_run: {rec.not_run})" if rec.not_run else "silent turn"}
    return {**out, "correct": True, "why": ""}


async def run_case(h: Harness, approach: str, case: dict[str, Any], trial: int, sem: asyncio.Semaphore):
    agent = AGENTS[approach]
    async with sem:
        chat, history, had_result = await setup(h, agent, case)
        rows = []
        for i, turn in enumerate(case["turns"]):
            history = history + [{"role": "user", "content": turn["user"]}]
            said = " ".join(m["content"] for m in history if m["role"] == "user")
            rec = await h.turn(agent, history, chat_id=chat)
            chat = rec.chat_id or chat
            rows.append({"approach": approach, "case": case["id"], "category": case["category"], "trial": trial,
                         "turn": i, "expect": turn["expect"], **score(rec, turn["expect"], had_result, said),
                         "record": rec.summary()})
            had_result = had_result or rec.ran
            history = history + [{"role": "assistant", "content": rec.text or ((rec.form or {}).get("form_config") or {}).get("description", "")}]
        return rows


def summarize(rows: list[dict[str, Any]], k: int, prices: tuple[float, float]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for approach in sorted({r["approach"] for r in rows}):
        ar = [r for r in rows if r["approach"] == approach]
        scored = [r for r in ar if r["category"] != "ambiguous"]
        calls = [r for r in scored if r["expect"]["action"] == "call"]
        asks = [r for r in scored if r["expect"]["action"] == "ask"]
        no_calls = [r for r in scored if r["expect"]["action"] == "no_call"]
        cases: dict[str, dict[int, list[bool]]] = {}
        for r in scored:
            cases.setdefault(r["case"], {}).setdefault(r["trial"], []).append(r["correct"])
        cats: dict[str, list[int]] = {}
        for r in scored:
            c = cats.setdefault(r["category"], [0, 0])
            c[0] += r["correct"]
            c[1] += 1
        usage = [r["record"]["usage"] for r in ar]
        tin = sum(u["input_tokens"] for u in usage)
        tout = sum(u["output_tokens"] for u in usage)
        by_label: dict[str, int] = {}
        for u in usage:
            for label, b in u["by_label"].items():
                by_label[label] = by_label.get(label, 0) + b["calls"]
        rec = lambda key: [r["record"][key] for r in ar]  # noqa: E731
        out[approach] = {
            "turn_accuracy": sum(r["correct"] for r in scored) / len(scored) if scored else None,
            "trigger_recall": sum(r["ran"] for r in calls) / len(calls) if calls else None,
            "args_accuracy": (sum(bool(r.get("args_ok")) for r in calls if r["ran"]) / max(1, sum(r["ran"] for r in calls))) if calls else None,
            "ask_accuracy": sum(r["correct"] for r in asks) / len(asks) if asks else None,
            "false_triggers": sum(r["ran"] or r["formed"] for r in no_calls),
            "false_trigger_rate": sum(r["ran"] or r["formed"] for r in no_calls) / len(no_calls) if no_calls else None,
            "silent_turns": sum(not r["visible"] for r in ar),
            "invented_recommendations": sum(r["invented"] for r in ar),
            "slot_hallucinations": sum(bool(r["hallucinated"]) for r in ar),
            "errors": sum(bool(r["error"]) for r in ar),
            "pass_k": f"{sum(len(t) == k and all(all(x) for x in t.values()) for t in cases.values())}/{len(cases)}",
            "by_category": {c: f"{ok}/{n}" for c, (ok, n) in sorted(cats.items())},
            "ambiguous": [f"{r['case']}#{r['trial']}: {'ran' if r['ran'] else 'form' if r['formed'] else 'no run'}"
                          for r in ar if r["category"] == "ambiguous"],
            "first_visible_p50_s": pct(rec("first_visible_s"), 0.5),
            "first_visible_p95_s": pct(rec("first_visible_s"), 0.95),
            "first_content_p50_s": pct(rec("first_content_s"), 0.5),
            "first_content_p95_s": pct(rec("first_content_s"), 0.95),
            "latency_p50_s": pct(rec("latency_s"), 0.5),
            "latency_p95_s": pct(rec("latency_s"), 0.95),
            "llm_calls_per_turn": round(sum(u["llm_calls"] for u in usage) / len(ar), 2),
            "llm_calls_by_caller": by_label,
            "tokens_per_turn": round((tin + tout) / len(ar)),
            "cost_usd": round((tin * prices[0] + tout * prices[1]) / 1e6, 4) if any(prices) else None,
        }
    return out


def decide(summary: dict[str, Any], margin: float = 0.01) -> str:
    a, b = summary.get("A"), summary.get("B")
    if not a or not b:
        return "n/a (need both approaches)"
    clean = a["false_triggers"] == 0 and a["invented_recommendations"] == 0 and a["slot_hallucinations"] == 0
    close = a["turn_accuracy"] >= b["turn_accuracy"] - margin
    verdict = "A" if clean and close else "B"
    return (f"{verdict}: A {a['turn_accuracy']:.1%} vs B {b['turn_accuracy']:.1%} "
            f"(within {margin:.0%}: {close}); A false triggers {a['false_triggers']}, invented "
            f"{a['invented_recommendations']}, slot hallucinations {a['slot_hallucinations']}")


METRICS = ["turn_accuracy", "trigger_recall", "args_accuracy", "ask_accuracy", "false_triggers",
           "false_trigger_rate", "silent_turns", "invented_recommendations", "slot_hallucinations", "errors",
           "pass_k", "first_visible_p50_s", "first_visible_p95_s", "first_content_p50_s", "first_content_p95_s",
           "latency_p50_s", "latency_p95_s", "llm_calls_per_turn", "llm_calls_by_caller", "tokens_per_turn", "cost_usd"]


def report(summary: dict[str, Any], rows: list[dict[str, Any]], args) -> str:
    approaches = list(summary)
    lines = [f"# Routing eval: A (planner-tool) vs B (planner-router), k={args.k}", "",
             f"**Decision rule:** {decide(summary, args.margin)}", "",
             "| metric | " + " | ".join(approaches) + " |", "|---|" + "---|" * len(approaches)]
    for m in METRICS:
        cells = []
        for a in approaches:
            v = summary[a][m]
            cells.append(f"{v:.1%}" if isinstance(v, float) and m.endswith(("accuracy", "recall", "rate")) else json.dumps(v) if isinstance(v, dict) else str(v))
        lines.append(f"| {m} | " + " | ".join(cells) + " |")
    lines += ["", "## By category", "", "| category | " + " | ".join(approaches) + " |", "|---|" + "---|" * len(approaches)]
    for c in sorted({c for a in approaches for c in summary[a]["by_category"]}):
        lines.append(f"| {c} | " + " | ".join(summary[a]["by_category"].get(c, "-") for a in approaches) + " |")
    lines += ["", "## Ambiguous (not scored)", ""] + [f"- **{a}**: {', '.join(summary[a]['ambiguous'])}" for a in approaches]
    lines += ["", "## Failures", ""]
    for r in rows:
        if r["category"] != "ambiguous" and not r["correct"]:
            rec = r["record"]
            lines.append(f"- **{r['approach']}** `{r['case']}` turn {r['turn']} trial {r['trial']}: {r['why']}. "
                         f"user: \"{rec['user'][:90]}\" reply: \"{rec['text'][:120]}\"")
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--approach", default="A,B")
    parser.add_argument("--k", type=int, default=5)
    parser.add_argument("--only", default="")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--margin", type=float, default=0.01, help="A wins if within this of B (use ~0.05 at k=1)")
    parser.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens")
    parser.add_argument("--price-out", type=float, default=0.0, help="USD per 1M output tokens")
    parser.add_argument("--dry-run", action="store_true", help="validate inputs, write the job plan, no model calls")
    args = parser.parse_args()

    cases = yaml.safe_load(DATA.read_text())
    if args.only:
        cases = [c for c in cases if c["id"] in set(args.only.split(","))]
    if args.dry_run:
        for c in cases:
            assert c["category"] and c["turns"] and all(t["expect"]["action"] in ("call", "ask", "no_call") for t in c["turns"]), c["id"]
        dry_run("routing", [{"approach": a, "case": c["id"], "turns": len(c["turns"]), "k": args.k} for a in args.approach.split(",") for c in cases])
        return
    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    async with Harness() as h:
        # Approaches run one after the other so their model calls never share a time window.
        for approach in args.approach.split(","):
            sem = asyncio.Semaphore(args.concurrency)
            jobs = [run_case(h, approach, c, t, sem) for c in cases for t in range(args.k)]
            rows += [row for case_rows in await asyncio.gather(*jobs) for row in case_rows]
    out = results_dir("routing")
    write_jsonl(out / "turns.jsonl", rows)
    summary = summarize(rows, args.k, (args.price_in, args.price_out))
    summary["decision"] = decide(summary, args.margin)
    (out / "summary.json").write_text(json.dumps(summary, indent=2))
    (out / "report.md").write_text(report({k: v for k, v in summary.items() if k != "decision"}, rows, args))
    print(json.dumps(summary, indent=2))
    print(f"\n{len(rows)} turns in {time.perf_counter() - started:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
