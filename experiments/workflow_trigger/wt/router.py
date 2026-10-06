"""Router: a small structured-output model call that classifies the turn.

It never decides to run anything. It reports the user's intent, any campaign
details stated in the conversation, and a verbatim quote as evidence; the gate
(wt.gate) decides in code.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field
from vel import Agent

Intent = Literal[
    "request_recommendation",
    "provide_details",
    "discuss_results",
    "general_chat",
    "decline",
]


class RouterDecision(BaseModel):
    intent: Intent
    vertical: Optional[Literal["Retail", "Finance", "Travel", "QSR", "Entertainment"]] = None
    kpi: Optional[Literal["ctr", "in_view_rate"]] = None
    geo: Optional[Literal["US", "EMEA", "APAC"]] = None
    budget: Optional[float] = None
    impression_goal: Optional[int] = None
    client_name: Optional[str] = None
    evidence_quote: str = Field(description="Shortest verbatim span of the LATEST user message that shows the intent")


ROUTER_PROMPT = """\
You classify one turn of a conversation between a media strategist and a \
media-planning assistant. You do not answer the user.

Intents for the LATEST user message:
- request_recommendation: asks for a product recommendation / media plan, gives a \
client brief to plan for, asks to run or re-run it, or changes a detail of a plan \
and wants it redone ("make it in-view instead", "run it again for EMEA").
- provide_details: answers a question the assistant asked about missing campaign \
details (e.g. "Retail", "about $25k", "click-through rate"), without a new request.
- discuss_results: asks about a recommendation that was already shown (why a \
product, what a number means, comparisons), without asking to re-run it.
- general_chat: greetings, thanks, small talk, how the assistant works, what a \
metric or product is, anything else.
- decline: says not to run it, to wait, or to cancel.

Campaign details: extract only what the user has stated anywhere in the \
conversation. vertical is one of Retail, Finance, Travel, QSR, Entertainment; kpi \
is ctr (click-through rate) or in_view_rate (in-view / viewability); geo is US, \
EMEA or APAC (map United States/America to US, Europe/UK/Middle East/Africa to \
EMEA, Asia/Australia/Japan to APAC; other regions stay null); budget in dollars \
($25k = 25000); impression_goal only if a minimum is stated. Use null for \
anything not stated. Never guess the vertical or the KPI.

evidence_quote: copy the shortest exact words from the latest user message that \
show its intent."""


def make_router(model: str = "gpt-4o-mini") -> Agent:
    return Agent(
        id="router",
        model={"provider": "openai", "model": model},
        system_prompt=ROUTER_PROMPT,
        output_type=RouterDecision,
        generation_config={"temperature": 0},
    )


def router_messages(history: list[dict[str, str]], state: dict[str, Any]) -> list[dict[str, str]]:
    """Recent conversation plus the facts the router needs from state."""
    context = {
        "known_details": state.get("slots", {}),
        "assistant_asked_for": state.get("asked_for", []),
        "recommendation_already_shown": bool(state.get("last_result")),
        "workflow_running": bool(state.get("workflow_running")),
    }
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history[-8:])
    return [
        {
            "role": "user",
            "content": f"State: {json.dumps(context)}\n\nConversation:\n{transcript}",
        }
    ]
