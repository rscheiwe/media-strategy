"""Approach B: a structured router + a code gate decide when the workflow runs.

Ported from experiments/workflow_trigger (v4: 100% turn accuracy on its 40-conversation set).
The router is a small structured-output call that only CLASSIFIES the turn (intent, stated
details, an evidence quote); the gate decides in code. "run" and "ask" both dispatch the same
workflow tool handler without an LLM choosing the tool (the DAG asks for missing details);
chat and discuss turns go to a chat agent that has no workflow tool.

Turn shape: a transient status at 0 ms, then a small-talk fast path (no router call) or the
router + gate.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field

from chat_api.workflow.domain import Slots

from ..brief_details import BRIEF_DETAILS
from ..turns import stream_with_empty_retry
from chat_api.workflow.steps import missing_slots, slot_valid

from ..follow_up import delivered, follow_up_events
from ..workflow_tool import SLOT_NAMES, dispatch

Intent = Literal["request_recommendation", "provide_details", "discuss_results", "general_chat", "decline"]


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
and wants it redone ("make it in-view instead", "what if the budget were $40k?").
  A message that describes a client's campaign (advertiser, vertical, geo, budget, \
KPI) is a client brief and counts as request_recommendation even if it asks nothing \
("Acme is a Retail advertiser in the US with $30k; CTR matters most").
  Asking to redo or change the plan without giving the new value ("with different \
values", "a different KPI", "change the budget") is also request_recommendation.
  Asking which product a client or brand should choose, use, buy or run ("what would \
suit a Finance brand optimizing for in-view?", "which product should a Travel brand use \
for viewability?") is request_recommendation, even with details missing: that is the \
decision the workflow makes. Asking what the data says ("which product has the best \
in-view rate in Finance?") is general_chat.
- provide_details: a short answer to a question the assistant asked about missing \
campaign details (e.g. "Retail", "about $25k", "click-through rate"), with no new brief.
- discuss_results: asks about a recommendation that was already shown (why a \
product, what a number means, comparisons), without asking to re-run it.
- general_chat: greetings, thanks, small talk, how the assistant works, what a \
metric or product is, data questions answered by analysis ("which product has the \
best CTR in Retail?"), and questions about one step of the analysis even when they \
name a vertical, KPI, geo or budget: the benchmarks for a vertical, how products rank on a \
KPI or what a budget buys, whether there's enough inventory in a geo for a budget ("is \
there scale in APAC for a Travel client with $18k?"), anything else.
- decline: says not to run it, to wait, or to cancel.

A short reply to the assistant's last question takes its meaning from that question. After \
"Would you like to re-run it with a different budget, or compare inventory for the top CTR products in EMEA and APAC?": \
"sure, $40k" or "try a different budget" is request_recommendation; "compare the regions" \
is general_chat.

Campaign details: extract them as <brief_details> below says.

evidence_quote: copy the shortest exact words from the latest user message that \
show its intent.

""" + BRIEF_DETAILS

INTRO_PROMPT = """\
You write the one or two sentences a media-planning assistant says at the start of a \
recommendation turn, so the user knows what's happening and why. The situation tells you \
which of three things is happening: the recommendation is running now; some details are \
still needed on the form below; or the details are pre-filled on the form below to confirm \
or change. Say that plainly and naturally, using the known details (vertical, KPI, geo, \
budget) where helpful. No lists, no product names, no results, and no numbers other than \
the known details."""

MISSING_WORDS = {"vertical": "vertical", "kpi": "primary KPI", "geo": "geo", "budget": "budget"}


def _readable(details: dict[str, Any]) -> dict[str, Any]:
    """Details as a strategist would say them: "CTR", "$15,000", "800,000 impressions"."""
    out = dict(details)
    if out.get("kpi"):
        out["kpi"] = {"ctr": "CTR", "in_view_rate": "in-view rate"}.get(out["kpi"], out["kpi"])
    if isinstance(out.get("budget"), (int, float)):
        out["budget"] = f"${out['budget']:,.0f}"
    if isinstance(out.get("impression_goal"), (int, float)):
        out["impression_goal"] = f"{out['impression_goal']:,.0f} impressions"
    return out


def _words(items: list[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def intro_fallback(gate: GateDecision, details: dict[str, Any]) -> str:
    if gate.action == "run":
        return "Running the recommendation workflow on those details now."
    if gate.action == "confirm":
        return "Here are the details I have so far; confirm or change any of them below and I'll run it."
    words = [MISSING_WORDS.get(m, m) for m in gate.missing] or ["details"]
    return f"Happy to put a recommendation together. I just need the {_words(words)}; fill them in below and I'll run it."


SMALL_TALK = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|thx|cheers|good (morning|afternoon|evening)|bye|goodbye|"
    r"great|perfect|awesome|cool|ok(ay)?|what (is|are|does)|what's|how (is|does|do|long)|"
    r"who|why is|can you explain|explain)\b",
    re.IGNORECASE,
)
CAMPAIGN_HINTS = re.compile(
    r"\d|\$|\bk\b|budget|recommend|plan\b|run\b|brief|retail|financ|travel|qsr|entertain|"
    r"\bus\b|emea|apac|europe|asia|ctr|click|in-view|viewab|impression|client|advertiser|brand",
    re.IGNORECASE,
)

Action = Literal["run", "ask", "confirm", "discuss", "status", "chat"]

# Brief details written in a message itself (not carried over from earlier turns).
DETAIL_HINTS = re.compile(
    r"\d|\$|retail|financ|\bbank|travel|airline|\bqsr\b|restaurant|entertain|\bus\b|\busa\b|emea|europe|apac|asia|"
    r"\bctr\b|click|in-?view|viewab",
    re.IGNORECASE,
)
# A detail the user names when asking to change it without a value ("a different KPI").
FIELD_HINTS = {
    "kpi": re.compile(r"\bkpi|metric|goal|objective|optimi[sz]", re.I),
    "budget": re.compile(r"budget|spend|money|\$", re.I),
    "geo": re.compile(r"\bgeo|region|market|country|location", re.I),
    "vertical": re.compile(r"vertical|industry|category|sector", re.I),
}


@dataclass
class GateDecision:
    action: Action
    args: dict[str, Any] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    highlight: list[str] = field(default_factory=list)  # confirm: fields the user wants to change


def is_small_talk(message: str, pending: bool) -> bool:
    """Obvious chat with no campaign details, and never while a request is pending."""
    return not pending and bool(SMALL_TALK.match(message)) and not CAMPAIGN_HINTS.search(message)


def _quote_in(quote: str, message: str) -> bool:
    def norm(text: str) -> str:
        return re.sub(r"[^a-z0-9$%]+", " ", text.lower()).strip()

    return bool(norm(quote)) and norm(quote) in norm(message)


def decide(decision: RouterDecision, latest: str, known: dict[str, Any], *, pending: bool, has_result: bool, running: bool) -> GateDecision:
    """Pick this turn's action in code from the router's classification and the chat state."""
    notes: list[str] = []
    stated = {}
    for name in SLOT_NAMES:
        value = getattr(decision, name)
        if value is not None and (name not in ("vertical", "kpi", "geo", "budget") or slot_valid(name, value)):
            stated[name] = value
    intent = decision.intent
    if intent == "request_recommendation" and not _quote_in(decision.evidence_quote, latest):
        # Without evidence in this message the turn can continue a pending request but not start one.
        notes.append(f"evidence quote not in message: {decision.evidence_quote!r}; treated as details")
        intent = "provide_details"
    if running:
        return GateDecision("status", notes=notes)
    if intent == "decline":
        return GateDecision("chat", notes=notes + ["declined"])
    wants_run = intent == "request_recommendation" or (intent == "provide_details" and pending)
    if wants_run:
        merged = {**known, **stated}
        missing = missing_slots(Slots(**{k: merged.get(k) for k in SLOT_NAMES}))
        args = {k: stated.get(k) for k in SLOT_NAMES}
        if missing:
            return GateDecision("ask", args=args, missing=missing, notes=notes)
        changed = [k for k in SLOT_NAMES if k in stated and stated[k] != known.get(k)]
        if has_result and not changed:
            # A redo with nothing new ("different values", "a different KPI"): let them change it.
            highlight = [f for f, rx in FIELD_HINTS.items() if rx.search(latest)]
            return GateDecision("confirm", args=args, notes=notes + ["change without a value"], highlight=highlight)
        if not has_result and not pending and not DETAIL_HINTS.search(latest):
            # Every detail comes from earlier turns ("let's run a workflow"): confirm before running.
            return GateDecision("confirm", args=args, notes=notes + ["details carried over"])
        return GateDecision("run", args=args, notes=notes)
    if intent == "discuss_results" and has_result:
        return GateDecision("discuss", notes=notes)
    return GateDecision("chat", notes=notes)


def router_messages(history: list[dict[str, str]], context: dict[str, Any]) -> list[dict[str, str]]:
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in history[-8:])
    return [{"role": "user", "content": f"State: {json.dumps(context)}\n\nConversation:\n{transcript}"}]


class WorkflowRouter:
    """Runs one Approach-B turn for a TurnRunner."""

    def __init__(self, model: dict | None = None) -> None:
        self.model = model
        self.last_decision: dict[str, Any] | None = None

    async def classify(self, history, context) -> RouterDecision:
        from vel import Agent  # type: ignore

        from ..agent import workflow_model_config

        # Temperature 0: the same turn should route the same way every time.
        router = Agent(id="workflow-router", model=self.model or workflow_model_config(), tools=[], system_prompt=ROUTER_PROMPT,
                       output_type=RouterDecision, generation_config={"temperature": 0})
        decision = None
        async for event in router.run_stream({"messages": router_messages(history, context)}, stateless=True):
            if isinstance(event, dict) and event.get("type") == "data-object-complete":
                decision = event["data"]["object"]
            elif isinstance(event, dict) and event.get("type") == "error":
                raise RuntimeError(event.get("errorText") or "router failed")
        if decision is None:
            raise RuntimeError("router returned no decision")
        return RouterDecision(**decision)

    async def run(self, runner, payload: dict[str, Any], **kwargs) -> AsyncIterator[dict[str, Any]]:
        yield {"type": "start"}
        yield {"type": "data-status", "id": "turn-status", "transient": True, "data": {"text": "Reading your message…"}}
        history = payload.get("messages") or []
        latest = next((m["content"] for m in reversed(history) if m.get("role") == "user"), "")
        state = runner.repo.get(runner.chat_id) if (runner.repo and runner.chat_id) else None
        pending = bool(state and state.pending_request)
        if is_small_talk(latest, pending):
            self.last_decision = {"fast_path": True, "action": "chat"}
            async for event in self._chat(runner, payload, **kwargs):
                yield event
            yield {"type": "finish"}
            return
        known = state.slot_values() if state else {}
        context = {
            "known_details": known,
            "assistant_asked_for": state.asked_for if state else [],
            "recommendation_already_shown": bool(state and state.last_run),
            "workflow_running": bool(state and state.running_since),
        }
        decision = await self.classify(history, context)
        gate = decide(decision, latest, known, pending=pending, has_result=bool(state and state.last_run), running=bool(state and state.running_since))
        self.last_decision = {"router": decision.model_dump(), "action": gate.action, "args": gate.args, "missing": gate.missing, "notes": gate.notes}
        if gate.action in ("run", "ask", "confirm"):
            # Every workflow turn opens by saying what's happening and why: running it, asking
            # for what's missing, or asking to confirm or change the details.
            details = {k: v for k, v in {**known, **gate.args}.items() if v not in (None, "")}
            async for event in self._intro(latest, gate, details):
                yield event
            ctx = runner._ctx("router")
            if gate.action == "confirm":
                ctx["confirm"] = gate.highlight
            else:
                yield {"type": "data-status", "id": "turn-status", "transient": True, "data": {"text": "Running the recommendation workflow…"}}
            rec = None
            async for event in dispatch(gate.args, ctx):
                rec = delivered(event, rec)
                yield event
            # Close a delivered recommendation with what they might do next.
            async for event in follow_up_events(rec, self.model):
                yield event
        elif gate.action == "status":
            yield {"type": "text-start", "id": "status-text"}
            yield {"type": "text-delta", "id": "status-text", "delta": "A recommendation is already running for this chat; it'll appear here when it finishes."}
            yield {"type": "text-end", "id": "status-text"}
        else:
            if "declined" in gate.notes and state and runner.repo:
                state.pending_request, state.asked_for, state.active_form_id = False, [], None
                runner.repo.save(state)
            async for event in self._chat(runner, payload, **kwargs):
                yield event
        yield {"type": "finish"}

    async def _intro(self, latest: str, gate: GateDecision, details: dict[str, Any] | None = None) -> AsyncIterator[dict[str, Any]]:
        """The sentence that opens a workflow turn, so the user knows what's happening and why.

        Streamed from the workflow model; if it fails or says nothing, a fixed sentence.
        """
        details = details if details is not None else {k: v for k, v in gate.args.items() if v not in (None, "")}
        situation = {
            "run": "All the details are known, so the recommendation is running now.",
            "ask": "Some details are still needed; the user fills them in on the form below.",
            "confirm": (
                "The user wants to change the plan but didn't say to what; the form below is "
                "pre-filled with the current details for them to change"
                + (f" (they mentioned: {', '.join(gate.highlight)})." if gate.highlight else ".")
                if "change without a value" in gate.notes
                else "These details were gathered from earlier in the conversation; the form below "
                "is pre-filled so the user can confirm or change them before it runs."
            ),
        }[gate.action]
        yield {"type": "text-start", "id": "form-intro"}
        said = False
        try:
            from vel import Agent  # type: ignore

            from ..agent import workflow_model_config

            agent = Agent(id="form-intro", model=self.model or workflow_model_config(), tools=[], system_prompt=INTRO_PROMPT,
                          policies={"max_steps": 1}, generation_config={"temperature": 0})
            content = json.dumps({"user_message": latest, "situation": situation, "known_details": _readable(details), "still_needed": gate.missing})
            async for event in agent.run_stream({"messages": [{"role": "user", "content": content}]}, stateless=True):
                if isinstance(event, dict) and event.get("type") == "text-delta" and event.get("delta"):
                    said = True
                    yield {"type": "text-delta", "id": "form-intro", "delta": event["delta"]}
        except Exception:  # noqa: BLE001 - the fixed sentence below covers any failure
            pass
        if not said:
            yield {"type": "text-delta", "id": "form-intro", "delta": intro_fallback(gate, details)}
        yield {"type": "text-end", "id": "form-intro"}

    async def _chat(self, runner, payload, **kwargs) -> AsyncIterator[dict[str, Any]]:
        agent = runner._build_agent()
        runner.prepare_agent(agent)
        async for event in stream_with_empty_retry(lambda: agent.run_stream(payload, **kwargs)):
            if isinstance(event, dict) and event.get("type") in ("start", "finish"):
                continue  # this turn's frame is ours
            yield event
