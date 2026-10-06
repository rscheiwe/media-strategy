"""The question that closes a recommendation turn: what the user might do next.

The options come from code (the brief's KPI, the other geos, the budget); the workflow model
only phrases them as one question. If the call fails or says nothing, a fixed question.
"""

from __future__ import annotations

import json
from typing import Any, AsyncIterator

from chat_api.workflow.domain import GEOS, KPI_LABELS

FOLLOW_UP_PROMPT = """\
You write the one short question a media-planning assistant asks right after showing a \
recommendation card, offering the next steps listed in `options`. Ask about those options \
only, in one natural sentence, using the details given. No results, no product names, no \
numbers other than the ones given, and no preamble ("Here's", "Great")."""


def follow_up_options(rec: dict[str, Any]) -> dict[str, Any]:
    """What the user could do next, from the delivered recommendation."""
    req = rec.get("requirements") or {}
    plan = rec.get("plan") or {}
    kpi = KPI_LABELS.get(req.get("kpi"), req.get("kpi") or "the KPI")
    others = [g for g in GEOS if g != req.get("geo")]
    budget = req.get("budget")
    budget_text = f"${budget:,.0f}" if isinstance(budget, (int, float)) else "the budget"
    if plan.get("kind") == "none":
        rerun = f"re-run it with a larger budget than {budget_text} or a lower impression goal"
    elif (plan.get("unspent") or 0) >= 1:
        rerun = f"re-run it with a different budget (part of {budget_text} went unspent)"
    else:
        rerun = f"re-run it with a different budget than {budget_text}"
    return {
        "vertical": req.get("vertical"),
        # Campaign history has no geo, so the KPI is the same in every region; what differs by
        # region is inventory, so that's the comparison worth offering.
        "options": [rerun, f"compare {req.get('vertical') or 'this vertical'} inventory for the top {kpi} products in {' and '.join(others)}"],
    }


def follow_up_fallback(options: dict[str, Any]) -> str:
    first, second = options["options"]
    return f"Would you like to {first}, or {second}?"


async def follow_up_events(rec: dict[str, Any] | None, model: dict | None = None) -> AsyncIterator[dict[str, Any]]:
    """Text events for the closing question; nothing when there's no recommendation."""
    if not rec:
        return
    options = follow_up_options(rec)
    yield {"type": "text-start", "id": "follow-up"}
    said = False
    try:
        from vel import Agent  # type: ignore

        from .agent import workflow_model_config

        agent = Agent(id="follow-up", model=model or workflow_model_config(), tools=[], system_prompt=FOLLOW_UP_PROMPT,
                      policies={"max_steps": 1}, generation_config={"temperature": 0})
        async for event in agent.run_stream({"messages": [{"role": "user", "content": json.dumps(options)}]}, stateless=True):
            if isinstance(event, dict) and event.get("type") == "text-delta" and event.get("delta"):
                said = True
                yield {"type": "text-delta", "id": "follow-up", "delta": event["delta"]}
    except Exception:  # noqa: BLE001 - the fixed question below covers any failure
        pass
    if not said:
        yield {"type": "text-delta", "id": "follow-up", "delta": follow_up_fallback(options)}
    yield {"type": "text-end", "id": "follow-up"}


def delivered(event: dict[str, Any], current: dict[str, Any] | None) -> dict[str, Any] | None:
    """Track the recommendation a dispatch delivered: its complete artifact snapshot."""
    if event.get("type") == "data-artifact-recommendation":
        data = event.get("data") or {}
        if data.get("status") == "complete" and data.get("payload"):
            return data["payload"]
    return current
