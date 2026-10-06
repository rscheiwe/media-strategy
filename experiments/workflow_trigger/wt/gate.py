"""The gate: decides in code what the main agent may do this turn.

Input: the router's classification and the conversation state. Output: run the
workflow (with validated arguments), ask for exactly the missing details,
discuss an earlier result, report that a run is in progress, or just chat.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from wt import pipeline
from wt.router import RouterDecision

Action = Literal["run", "ask", "discuss", "status", "chat"]
SLOT_FIELDS = ("vertical", "kpi", "geo", "budget", "impression_goal", "client_name")


@dataclass
class GateDecision:
    action: Action
    args: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def decide(decision: RouterDecision, latest_user_message: str, state: dict[str, Any]) -> GateDecision:
    """Merge stated details into state and pick this turn's action."""
    notes: list[str] = []
    slots = state.setdefault("slots", {})
    for name in SLOT_FIELDS:
        value = getattr(decision, name)
        if value is not None and (name not in pipeline.REQUIRED_SLOTS or pipeline._valid(name, value)):
            slots[name] = value

    intent = decision.intent
    if intent == "request_recommendation" and not _quote_in(decision.evidence_quote, latest_user_message):
        # Without evidence in this message the turn can continue a pending request
        # (it's answering our question) but can't start a new one.
        notes.append(f"evidence quote not in message: {decision.evidence_quote!r}; treated as details")
        intent = "provide_details"

    if state.get("workflow_running"):
        return GateDecision("status", notes=notes)
    if intent == "decline":
        state["pending_request"] = False
        return GateDecision("chat", notes=notes)

    wants_run = intent == "request_recommendation" or (
        intent == "provide_details" and state.get("pending_request")
    )
    if wants_run:
        missing = pipeline.missing_slots(slots)
        if missing:
            state["pending_request"] = True
            state["asked_for"] = missing
            return GateDecision("ask", missing=missing, notes=notes)
        state["pending_request"] = False
        state["asked_for"] = []
        return GateDecision("run", args={k: slots.get(k) for k in SLOT_FIELDS}, notes=notes)

    if intent == "discuss_results" and state.get("last_result"):
        return GateDecision("discuss", notes=notes)
    return GateDecision("chat", notes=notes)


def _quote_in(quote: str, message: str) -> bool:
    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9$%]+", " ", text.lower()).strip()

    return bool(norm(quote)) and norm(quote) in norm(message)
