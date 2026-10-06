"""Eval (e): explanation faithfulness of the workflow's LLM rationale.

    cd app/api && PYTHONPATH=. uv run python -m evals.run_explain --k 3

For the 5 complete briefs plus briefs 6 and 7 (completed as the form would), the deterministic
Recommendation is computed once, then the explain step's raw LLM text is sampled k times:
  - an LLM judge (default gpt-5.4, no reasoning effort) scores each text per criterion:
    names the pick; gives the correct reason the KPI leader was rejected; mentions a rejected
    alternative; states any tradeoff in the right direction; no contradiction of the plan;
    no invented product;
  - spotcheck.md puts every brief's facts, first text and verdict side by side for a human to
    audit the judge.
Targets: judge >= 95% per criterion and 100% on the leader's rejection reason.
Live model calls. Writes results/<ts>/explain/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from typing import Any

from pydantic import BaseModel, Field

from chat_api.casedata import connect_readonly, ensure_case_db
from chat_api.chat.agent import model_config
from chat_api.workflow.domain import Recommendation, Slots
from chat_api.workflow.explain import check, explain_input, to_rationale, vel_explainer
from chat_api.workflow.steps import recommend

from dotenv import load_dotenv

from .harness import APP_ROOT, dry_run, results_dir, write_jsonl

BRIEFS: dict[str, dict[str, Any]] = {
    "Acme": {"client_name": "Acme Shoes", "vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30000},
    "Burger": {"client_name": "Burger Bazaar", "vertical": "QSR", "kpi": "in_view_rate", "geo": "US", "budget": 15000, "impression_goal": 800000},
    "CineVerse": {"client_name": "CineVerse", "vertical": "Entertainment", "kpi": "ctr", "geo": "EMEA", "budget": 40000},
    "Stellar": {"client_name": "Stellar Bank", "vertical": "Finance", "kpi": "ctr", "geo": "US", "budget": 20000, "impression_goal": 800000},
    "Wanderlust": {"client_name": "Wanderlust Air", "vertical": "Travel", "kpi": "in_view_rate", "geo": "APAC", "budget": 18000, "impression_goal": 850000},
    "Brief6+Entertainment": {"client_name": "new mobile app", "vertical": "Entertainment", "kpi": "in_view_rate", "geo": "US", "budget": 25000},
    "Brief7+CTR": {"vertical": "Finance", "kpi": "ctr", "geo": "EMEA", "budget": 25000},
}

REASONS = {
    "INVENTORY_BELOW_BUDGET": "the forecast inventory can't absorb the full budget",
    "BUDGET_BELOW_GOAL": "the budget buys fewer impressions than the goal at its CPM",
    "INVENTORY_BELOW_GOAL": "the forecast inventory is below the impression goal",
    "BELOW_MIN_CONFIDENCE": "its forecast confidence is below the policy minimum",
}

JUDGE_PROMPT = """\
You audit a short rationale written for a media recommendation. You get the FACTS (the
deterministic decision, the source of truth) and the TEXT. Judge only against the FACTS.

Criteria (true / false; null when it doesn't apply):
- names_pick: the TEXT names every product in the recommended plan.
- leader_reason_correct: applies only if FACTS has a rejected_leader. True if the TEXT says
  that product wasn't chosen AND gives a reason consistent with its listed reasons. False if it
  omits the leader or gives a wrong or contradictory reason.
- mentions_rejected_alternative: applies only if FACTS has a rejected_leader (a product ranked
  above the pick); true if the TEXT mentions at least one such product as not chosen.
- tradeoff_direction_correct: applies if the TEXT states a tradeoff (e.g. a bundle would score
  higher, a near-tie, budget left unspent, a goal margin); true if every stated tradeoff has
  the direction the FACTS imply. Null if no tradeoff is stated.
- no_contradiction: true if nothing in the TEXT contradicts the FACTS (amounts, which product
  is in the plan, whether the goal is met, single vs bundle).
- no_invented_product: true if every product the TEXT names appears in the FACTS.
Give a one-sentence note on any false criterion.
"""


class Verdict(BaseModel):
    names_pick: bool
    leader_reason_correct: bool | None = None
    mentions_rejected_alternative: bool | None = None
    tradeoff_direction_correct: bool | None = None
    no_contradiction: bool
    no_invented_product: bool
    note: str = Field(default="")


CRITERIA = [f for f in Verdict.model_fields if f != "note"]


def facts(rec: Recommendation) -> dict[str, Any]:
    names = {c.product_id: c.product_name for c in rec.candidates}
    leader = next((a for a in rec.alternatives if a.kind == "rejected_leader"), None)
    return {
        "explainer_input": explain_input(rec),
        "recommended_plan": [line.product_name for line in rec.plan.lines],
        "rejected_leader": ({"product": names[leader.product_ids[0]], "reasons": [REASONS.get(r, r) for r in leader.reasons]}
                            if leader else None),
        "all_products": sorted(names.values()),
    }


async def judge(model: dict[str, Any], fact: dict[str, Any], text: str) -> Verdict:
    from vel import Agent  # type: ignore

    agent = Agent(id="explain-judge", model=model, tools=[], system_prompt=JUDGE_PROMPT, output_type=Verdict)
    content = f"FACTS:\n{json.dumps(fact, indent=1)}\n\nTEXT:\n{text}"
    async for event in agent.run_stream({"messages": [{"role": "user", "content": content}]}, stateless=True):
        if isinstance(event, dict) and event.get("type") == "data-object-complete":
            return Verdict(**event["data"]["object"])
        if isinstance(event, dict) and event.get("type") == "error":
            raise RuntimeError(event.get("errorText") or "judge failed")
    raise RuntimeError("judge returned nothing")


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--k", type=int, default=3)
    parser.add_argument("--judge-model", default="gpt-5.4")
    parser.add_argument("--concurrency", type=int, default=6)
    parser.add_argument("--dry-run", action="store_true", help="validate inputs, write the job plan, no model calls")
    args = parser.parse_args()
    load_dotenv(APP_ROOT / ".env")

    conn = connect_readonly(ensure_case_db())
    recs = {name: recommend(conn, Slots(**slots)) for name, slots in BRIEFS.items()}
    conn.close()
    if args.dry_run:
        dry_run("explain", [{"brief": n, "k": args.k, "pick": [line.product_name for line in r.plan.lines], "facts": facts(r)} for n, r in recs.items()])
        return
    explainer = vel_explainer(model_config())
    judge_model = {**model_config(), "model": args.judge_model}
    sem = asyncio.Semaphore(args.concurrency)

    async def one(name: str, trial: int) -> dict[str, Any]:
        async with sem:
            rec = recs[name]
            row: dict[str, Any] = {"brief": name, "trial": trial, "pick": [line.product_name for line in rec.plan.lines]}
            try:
                out = await explainer(rec)
                row["schema_problems"] = check(out, rec)
                r = to_rationale(out, rec)
                # The card's model-written parts, as one text for the judge.
                text = " ".join([r.best_fit or ""] + [n.explanation for n in r.rejected])
            except Exception as exc:  # noqa: BLE001
                return {**row, "error": f"explain: {exc}"}
            row["text"] = text
            try:
                v = await judge(judge_model, facts(rec), text)
                row["verdict"] = v.model_dump()
            except Exception as exc:  # noqa: BLE001
                row["error"] = f"judge: {exc}"
            return row

    started = time.perf_counter()
    rows = await asyncio.gather(*[one(n, t) for n in BRIEFS for t in range(args.k)])
    out = results_dir("explain")
    write_jsonl(out / "texts.jsonl", rows)

    judged = [r for r in rows if "verdict" in r]
    summary: dict[str, Any] = {
        "texts": len(rows),
        "errors": sum("error" in r for r in rows),
        "schema_problems": sum(bool(r.get("schema_problems")) for r in rows),
        "judge": {},
        "judge_failures": [],
    }
    for c in CRITERIA:
        applicable = [r for r in judged if r["verdict"][c] is not None]
        summary["judge"][c] = f"{sum(r['verdict'][c] for r in applicable)}/{len(applicable)}" if applicable else "n/a"
    for r in judged:
        bad = [c for c in CRITERIA if r["verdict"][c] is False]
        if bad:
            summary["judge_failures"].append(f"{r['brief']}#{r['trial']}: {bad} — {r['verdict']['note']}")
    (out / "summary.json").write_text(json.dumps(summary, indent=2))

    lines = ["# Explanation spot-check (human audit of the judge)", "",
             "For each brief: the decision facts, the first sampled text and the judge. "
             "Tick the box if you agree with the judge on every criterion.", ""]
    for name in BRIEFS:
        r = next((x for x in rows if x["brief"] == name and "text" in x), None)
        f = facts(recs[name])
        lines += [f"## {name}", "", f"- plan: {', '.join(f['recommended_plan'])}",
                  f"- rejected leader: {f['rejected_leader']}", ""]
        if r:
            lines += ["> " + r["text"].replace("\n", "\n> "), "",
                      f"- judge: {json.dumps(r.get('verdict'))}", "", "- [ ] I agree with the judge", ""]
    (out / "spotcheck.md").write_text("\n".join(lines))
    print(json.dumps(summary, indent=2))
    print(f"\n{len(rows)} texts in {time.perf_counter() - started:.0f}s -> {out}")


if __name__ == "__main__":
    asyncio.run(main())
