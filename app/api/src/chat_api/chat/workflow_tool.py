"""`run_recommendation`: the recommendation workflow behind a tool.

One async-generator handler serves both trigger approaches:
- Approach A: the chat agent calls the tool; vel passes our data-* events through and, with
  `stop_on_first_use`, ends the turn on the tool output (the form or the recommendation card
  IS the answer, so no second LLM pass restates the numbers).
- Approach B and form submissions: `dispatch()` runs the same handler directly and wraps it in
  a synthetic tool part, so the UI renders both paths identically.

The handler owns the per-chat workflow state (chat_workflow_state): merging details from their
sources, the pending request and active form (the human-in-the-loop pause), the run lock and
idempotent replay. The DAG itself (workflow/dag.py) is stateless per run.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

from vel.tools.registry import ToolSpec  # type: ignore

from chat_api.workflow.domain import GEOS, KPIS, VERTICALS, Slots
from chat_api.workflow.policy import inventory_policy, selection_policy
from chat_api.workflow.service import _artifact, artifact_title, run_workflow

TOOL_NAME = "run_recommendation"
LOCK_TTL = timedelta(seconds=120)
SLOT_NAMES = ("client_name", "vertical", "kpi", "geo", "budget", "impression_goal")

# Lexical support for the grounding check: a vertical or KPI the user never wrote (or a clear
# synonym of) is asked for, pre-selected, instead of trusted. Catches a model's guess.
SUPPORT = {
    "vertical": {
        "Retail": r"retail|shop|store|e-?commerce|apparel|shoe|fashion",
        "Finance": r"financ|bank|insur|fintech|credit|loan",
        "Travel": r"travel|airline|hotel|flight|tourism|vacation|cruise",  # not "air": brand names
        "QSR": r"\bqsr\b|quick.?service|fast.?food|restaurant|burger|pizza",
        "Entertainment": r"entertain|movie|film|cinema|stream|music|gaming|tv\b",
    },
    "kpi": {
        "ctr": r"\bctr\b|click",
        "in_view_rate": r"in.?view|viewab|visib|attention",
    },
}

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "client_name": {"type": ["string", "null"], "description": "Client or advertiser name, if stated."},
        "vertical": {"type": ["string", "null"], "enum": [*VERTICALS, None], "description": "Only if the user stated it."},
        "kpi": {"type": ["string", "null"], "enum": [*KPIS, None], "description": "ctr (click-through rate) or in_view_rate. Only if stated."},
        "geo": {"type": ["string", "null"], "enum": [*GEOS, None]},
        "budget": {"type": ["number", "null"], "description": "Budget in USD, e.g. 25000."},
        "impression_goal": {"type": ["integer", "null"], "description": "Total impressions required, if stated."},
    },
    "required": list(SLOT_NAMES),
}

DESCRIPTION = (
    "Run the recommendation workflow: benchmarks products for the client's vertical, estimates "
    "impressions from budget and CPM, checks inventory for the geo, ranks viable products by the "
    "primary KPI and returns a recommendation with rejected alternatives and tradeoffs. Call it "
    "whenever the user shares a client brief or asks for a recommendation or plan, EVEN IF details "
    "are missing: pass null for anything not stated and never guess the vertical or KPI; the "
    "workflow asks the user for what's missing. Also call it again when the user changes a detail "
    "(\"what if the budget were $40k?\")."
)


def _now() -> datetime:
    return datetime.now(UTC)


def merge_slots(stored: dict[str, dict], incoming: dict[str, Any], source: str, turn: int) -> dict[str, dict]:
    """Stored details updated by the incoming ones; a null never overwrites a known value."""
    merged = dict(stored)
    for name in SLOT_NAMES:
        value = incoming.get(name)
        if value in (None, ""):
            continue
        if name == "budget":
            value = float(value)
        if name == "impression_goal":
            value = int(value)
        if (merged.get(name) or {}).get("value") == value:
            continue  # re-sent unchanged (the agent passes every known detail): keep its origin
        merged[name] = {"value": value, "source": source, "turn": turn}
    return merged


def ungrounded(slots: dict[str, dict], user_text: str, turn: int) -> list[str]:
    """Vertical / KPI values a model set this turn whose wording isn't in this turn's message.

    Details from earlier turns were checked when they were set (or confirmed in the form), so
    a reply like "CTR" doesn't un-ground the vertical the brief stated a turn before.
    """
    out = []
    text = user_text.lower()
    for name in ("vertical", "kpi"):
        entry = slots.get(name)
        if not entry or entry.get("source") == "form" or entry.get("turn") != turn:
            continue
        pattern = SUPPORT[name].get(entry.get("value"))
        if pattern and not re.search(pattern, text):
            out.append(name)
    return out


def idem_key(chat_id: str, slots: Slots) -> str:
    payload = json.dumps(
        {"chat": chat_id, "slots": slots.model_dump(), "inv": inventory_policy().version, "sel": selection_policy().version},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def summary(rec) -> dict[str, Any]:
    return {
        "headline": rec.rationale.text.split(". ")[0][:200],
        "kind": rec.plan.kind,
        "lines": [{"name": line.product_name, "spend": line.spend, "impressions": line.impressions} for line in rec.plan.lines],
        "blended_kpi": rec.plan.blended_kpi,
        "meets_goal": rec.plan.meets_goal,
    }


async def run_recommendation(inp: dict, ctx: dict) -> AsyncIterator[dict[str, Any]]:
    """The tool handler. ctx: chat_id, workflow_state (repo), explainer, user_text, turn, slot_source."""
    repo = ctx.get("workflow_state")
    chat_id = ctx.get("chat_id") or "no-chat"
    state = repo.get(chat_id) if repo else None
    if state and state.running_since and _now() - state.running_since < LOCK_TTL:
        yield {"type": "tool-output", "output": {"status": "not_run", "reason": "already_running"}}
        return

    source = ctx.get("slot_source") or "llm"
    stored = state.slots if state else {}
    slots_state = merge_slots(stored, inp or {}, source, int(ctx.get("turn") or 0))
    slots = Slots(**{name: entry.get("value") for name, entry in slots_state.items()})
    turn = int(ctx.get("turn") or 0)
    unconfirmed = ungrounded(slots_state, ctx.get("user_text") or "", turn) if source != "form" else []

    confirm = ctx.get("confirm")  # None, or the fields to highlight on a confirm-or-change form

    # Idempotent replay: the same chat, details and policy reuse the last result.
    key = idem_key(chat_id, slots)
    if state and state.last_run and state.last_run.get("idem_key") == key and not unconfirmed and confirm is None:
        last = state.last_run
        yield _artifact(last["run_id"], "complete", artifact_title(slots), last["recommendation"], 1, int(_now().timestamp() * 1000))
        yield {"type": "tool-output", "output": {"status": "done", "run_id": last["run_id"], "artifact_id": last["artifact_id"], "replayed": True, "summary": last["summary"]}}
        return

    if state and repo:
        state.slots, state.running_since = slots_state, _now()
        state = repo.save(state)
    run_id = uuid.uuid4().hex[:12]
    result: dict[str, Any] = {"status": "failed", "error": "workflow produced no result"}
    try:
        async for event in run_workflow(slots, run_id=run_id, unconfirmed=unconfirmed, confirm=confirm, explainer=ctx.get("explainer")):
            if event["type"] == "workflow-result":
                result = event
            else:
                yield event
    finally:
        if state and repo:
            state.running_since = None
            if result["status"] == "FORM_REQUIRED":
                state.pending_request, state.asked_for, state.active_form_id = True, result["missing"], result["form_id"]
            elif result["status"] == "done":
                rec = result["recommendation"]
                state.pending_request, state.asked_for, state.active_form_id = False, [], None
                state.last_run = {
                    "run_id": run_id,
                    "idem_key": key,
                    "artifact_id": result["artifact_id"],
                    "policy_version": rec.policy["version"],
                    "summary": summary(rec),
                    "recommendation": rec.model_dump(mode="json"),
                }
            repo.save(state)

    if result["status"] == "FORM_REQUIRED":
        yield {"type": "tool-output", "output": {k: result[k] for k in ("status", "form_id", "missing", "form_config")}}
    elif result["status"] == "done":
        rec = result["recommendation"]
        yield {"type": "tool-output", "output": {"status": "done", "run_id": run_id, "artifact_id": result["artifact_id"], "summary": summary(rec)}}
    else:
        yield {"type": "tool-output", "output": {"status": "failed", "error": result.get("error")}}


RUN_RECOMMENDATION = ToolSpec(
    name=TOOL_NAME,
    description=DESCRIPTION,
    input_schema=INPUT_SCHEMA,
    output_schema={"type": "object"},
    handler=run_recommendation,
    parallel_safe=False,
)


async def dispatch(args: dict, ctx: dict) -> AsyncIterator[dict[str, Any]]:
    """Run the workflow without an LLM choosing the tool, framed as a tool part for the UI."""
    call_id = f"call-{uuid.uuid4().hex[:8]}"
    yield {"type": "tool-input-start", "toolCallId": call_id, "toolName": TOOL_NAME}
    yield {"type": "tool-input-available", "toolCallId": call_id, "toolName": TOOL_NAME, "input": args}
    async for event in run_recommendation(args, ctx):
        if event.get("type") == "tool-output":
            yield {"type": "tool-output-available", "toolCallId": call_id, "output": event["output"]}
        else:
            yield event
