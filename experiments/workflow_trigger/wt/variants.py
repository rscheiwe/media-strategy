"""Three strategies for "chat vs. run the workflow", as turn handlers.

- v1 prompt-only: one agent, tool always offered, tool_choice auto; the prompt
  carries the whole trigger policy.
- v2 router + gate + forced tool: a router classifies the turn, code decides,
  and the main agent is offered the tool (forced) only when the gate says run.
- v3 router + gate + direct dispatch: same gate, but code runs the workflow
  itself; the agent never chooses the tool and only chats or narrates.

Each handler is an async generator of AI SDK UI-stream chunks (start/finish are
added by the caller) and records a TurnRecord on the conversation.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import Any

from vel import Agent

from wt import prompts
from wt.gate import GateDecision, decide
from wt.router import RouterDecision, make_router, router_messages
from wt.workflow_tool import TOOL_NAME, make_tool, stream_workflow

VARIANTS = ("v1", "v2", "v3", "v4")
VISIBLE = {"data-status", "text-delta", "data-workflow-stage", "tool-input-start", "tool-input-available"}
CONTENT = {"text-delta", "data-workflow-stage", "tool-input-start", "tool-input-available"}
# Chunk types forwarded to the client; vel's start/finish are per agent call
FORWARD = {
    "start-step", "finish-step", "text-start", "text-delta", "text-end",
    "reasoning-start", "reasoning-delta", "reasoning-end",
    "tool-input-start", "tool-input-delta", "tool-input-available",
    "tool-output-available", "tool-output-error", "error",
}


@dataclass
class Settings:
    model: str = "gpt-4o-mini"
    router_model: str = "gpt-4o-mini"
    upstream: Callable[[], Any] | None = None  # httpx client factory
    agent_factory: Callable[..., Agent] | None = None  # tests inject scripted agents


@dataclass
class TurnRecord:
    variant: str
    user: str
    text: str = ""
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    stages: list[dict[str, Any]] = field(default_factory=list)
    router: dict[str, Any] | None = None
    gate: dict[str, Any] | None = None
    usage: list[dict[str, Any]] = field(default_factory=list)
    latency_s: float = 0.0
    first_visible_s: float | None = None  # first chunk the user can see (status, text, tool, stage)
    first_content_s: float | None = None  # first real content (text, stage, or the tool call)
    router_s: float | None = None
    router_skipped: str | None = None
    speculative: str | None = None  # v4: "used" | "discarded"
    error: str | None = None

    @property
    def ran_workflow(self) -> bool:
        return any(c["name"] == TOOL_NAME and (c.get("output") or {}).get("status") == "done"
                   for c in self.tool_calls)


@dataclass
class Conversation:
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    history: list[dict[str, str]] = field(default_factory=list)
    state: dict[str, Any] = field(default_factory=dict)
    turns: list[TurnRecord] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.state.setdefault("conversation_id", self.id)
        self.state.setdefault("slots", {})


async def run_turn(
    variant: str, convo: Conversation, user_message: str, settings: Settings
) -> AsyncIterator[dict[str, Any]]:
    """Handle one user turn with the given strategy, streaming UI chunks."""
    record = TurnRecord(variant=variant, user=user_message)
    convo.history.append({"role": "user", "content": user_message})
    started = time.perf_counter()
    try:
        handler = {"v1": _v1, "v2": _v2, "v3": _v3, "v4": _v4}[variant]
        async for chunk in handler(convo, record, settings):
            elapsed = time.perf_counter() - started
            if record.first_visible_s is None and chunk.get("type") in VISIBLE:
                record.first_visible_s = elapsed
            if record.first_content_s is None and chunk.get("type") in CONTENT:
                record.first_content_s = elapsed
            yield chunk
    except Exception as exc:  # recorded for the eval; surfaced as one error chunk
        record.error = f"{type(exc).__name__}: {exc}"
        yield {"type": "error", "errorText": record.error}
    finally:
        record.latency_s = time.perf_counter() - started
        convo.turns.append(record)
        reply = record.text or ("(ran the recommendation workflow)" if record.ran_workflow else "")
        convo.history.append({"role": "assistant", "content": reply})


# ------------------------------------------------------------------ variants

async def _v1(convo, record, settings) -> AsyncIterator[dict[str, Any]]:
    agent = _agent(
        settings, "assistant",
        prompts.BASE + prompts.V1_POLICY + prompts.context_block(convo.state),
        tools=True, convo=convo,
    )
    async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
        yield chunk


async def _v2(convo, record, settings) -> AsyncIterator[dict[str, Any]]:
    gate = await _route(convo, record, settings)
    if gate.action == "run":
        system = prompts.BASE + prompts.context_block(convo.state) + "\n\n" + \
            prompts.TURN_INSTRUCTIONS["run_forced"].format(args=json.dumps(gate.args))
        agent = _agent(settings, "assistant", system, tools=True, convo=convo,
                       tool_choice={"type": "function", "name": TOOL_NAME})
    else:
        agent = _agent(settings, "assistant", _instructed(convo, gate), tools=False, convo=convo)
    async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
        yield chunk


async def _v3(convo, record, settings) -> AsyncIterator[dict[str, Any]]:
    gate = await _route(convo, record, settings)
    if gate.action == "run":
        async for chunk in _dispatch(gate.args, convo, record, settings):
            yield chunk
        system = prompts.BASE + prompts.context_block(convo.state) + "\n\n" + prompts.TURN_INSTRUCTIONS["narrate"]
        agent = _agent(settings, "narrator", system, tools=False, convo=convo)
    else:
        agent = _agent(settings, "assistant", _instructed(convo, gate), tools=False, convo=convo)
    async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
        yield chunk


async def _v4(convo, record, settings) -> AsyncIterator[dict[str, Any]]:
    """v3's decisions, tuned for time-to-first-output.

    1. A transient status part goes out immediately.
    2. The router is skipped when its answer can't matter: a run is in progress
       (the gate always says "status"), or the message is plain small talk with
       no campaign details.
    3. Otherwise the router and a tool-less reply start together; the reply is
       held until the gate decides, then streamed (chat/discuss) or discarded.
    """
    yield {"type": "data-status", "id": "turn-status", "data": {"text": "Reading your message"}, "transient": True}

    message = convo.history[-1]["content"]
    if convo.state.get("workflow_running"):
        record.router_skipped = "workflow running"
        record.gate = {"action": "status", "args": {}, "missing": [], "notes": ["router skipped"]}
        agent = _agent(settings, "assistant", _instructed(convo, GateDecision("status")), tools=False, convo=convo)
        async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
            yield chunk
        return
    if is_small_talk(message, convo.state):
        record.router_skipped = "small talk"
        record.gate = {"action": "chat", "args": {}, "missing": [], "notes": ["router skipped"]}
        agent = _agent(settings, "assistant", _instructed(convo, GateDecision("chat")), tools=False, convo=convo)
        async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
            yield chunk
        return

    # Speculative reply in parallel with the router; held until the gate decides
    speculative = _agent(settings, "assistant", _speculative_prompt(convo), tools=False, convo=convo)
    buffer: asyncio.Queue = asyncio.Queue()
    spec_record = TurnRecord(variant="v4-spec", user=message)

    async def produce() -> None:
        try:
            async for chunk in _stream_agent(speculative, convo.history, spec_record, prefix="assistant"):
                await buffer.put(chunk)
        finally:
            await buffer.put(None)

    spec_task = asyncio.create_task(produce())
    try:
        gate = await _route(convo, record, settings)
    except BaseException:
        spec_task.cancel()
        raise

    if gate.action in ("chat", "discuss"):
        # Streamed text can't be taken back, so check the whole speculative reply
        # first (it has usually finished by the time the router returns).
        await spec_task
        held: list[dict[str, Any]] = []
        while (chunk := buffer.get_nowait()) is not None:
            held.append(chunk)
        if unbacked_recommendation(spec_record.text, convo.state):
            record.speculative = "rejected"
            record.usage += [{**u, "role": "speculative"} for u in spec_record.usage]
            agent = _agent(settings, "assistant", _instructed(convo, gate), tools=False, convo=convo)
            async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
                yield chunk
            return
        record.speculative = "used"
        for chunk in held:
            yield chunk
        record.text = spec_record.text
        record.usage += spec_record.usage
        return

    record.speculative = "discarded"
    spec_task.cancel()
    try:
        await spec_task
    except asyncio.CancelledError:
        pass
    record.usage += [{**u, "role": "speculative"} for u in spec_record.usage]
    if gate.action == "run":
        yield {"type": "data-status", "id": "turn-status", "data": {"text": "Running the recommendation"}, "transient": True}
        async for chunk in _dispatch(gate.args, convo, record, settings):
            yield chunk
        system = prompts.BASE + prompts.context_block(convo.state) + "\n\n" + prompts.TURN_INSTRUCTIONS["narrate"]
        agent = _agent(settings, "narrator", system, tools=False, convo=convo)
    else:
        agent = _agent(settings, "assistant", _instructed(convo, gate), tools=False, convo=convo)
    async for chunk in _stream_agent(agent, convo.history, record, prefix="assistant"):
        yield chunk


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


def is_small_talk(message: str, state: dict[str, Any]) -> bool:
    """Conservative pre-filter: only obvious chat with no campaign details, and
    never while a request is pending (the reply might be an answer to us)."""
    if state.get("pending_request"):
        return False
    return bool(SMALL_TALK.match(message)) and not CAMPAIGN_HINTS.search(message)


def _speculative_prompt(convo: Conversation) -> str:
    """Instruction for the reply drafted while the router runs: valid for both
    chat and discuss turns, never a recommendation."""
    about_result = (
        "If the user asks about the latest recommendation, answer from it. "
        if convo.state.get("last_result")
        else "No recommendation has been made in this conversation yet; never describe one. "
    )
    return (
        prompts.BASE + prompts.context_block(convo.state) + "\n\n# This turn\n"
        "Reply conversationally. " + about_result +
        "Do not recommend products, splits or budgets, and do not start or promise a new recommendation."
    )


PRODUCT_NAMES = ("display plus", "video spotlight", "commerce connect", "social amplifier",
                 "premium takeover", "attention builder")


def unbacked_recommendation(text: str, state: dict[str, Any]) -> bool:
    """A reply that recommends products with no workflow result behind it."""
    if state.get("last_result"):
        return False
    lowered = text.lower()
    return any(p in lowered for p in PRODUCT_NAMES) and any(
        w in lowered for w in ("recommend", "suggest", "use the", "allocate", "split")
    )


# ------------------------------------------------------------------ pieces

async def _route(convo: Conversation, record: TurnRecord, settings: Settings) -> GateDecision:
    """Router call (structured output) then the code gate."""
    router = (settings.agent_factory or _default_agent)(
        role="router", settings=settings, system=None, tools=None, tool_context=None,
        generation_config=None,
    )
    decision_obj = None
    router_started = time.perf_counter()
    async for event in router.run_stream({"messages": router_messages(convo.history, convo.state)}):
        if event.get("type") == "data-object-complete":
            decision_obj = event["data"]["object"]
        elif event.get("type") == "response-metadata" and event.get("usage"):
            record.usage.append({"role": "router", **event["usage"]})
        elif event.get("type") == "error":
            raise RuntimeError(f"router failed: {event.get('errorText')}")
    record.router_s = time.perf_counter() - router_started
    if decision_obj is None:
        raise RuntimeError("router returned no decision")
    decision = RouterDecision(**decision_obj)
    gate = decide(decision, convo.history[-1]["content"], convo.state)
    record.router = decision.model_dump()
    record.gate = {"action": gate.action, "args": gate.args, "missing": gate.missing, "notes": gate.notes}
    return gate


async def _dispatch(args, convo, record, settings) -> AsyncIterator[dict[str, Any]]:
    """V3: code runs the workflow and emits it as a tool part (no LLM choice)."""
    call_id = f"call-{uuid.uuid4().hex[:8]}"
    yield {"type": "tool-input-start", "toolCallId": call_id, "toolName": TOOL_NAME}
    yield {"type": "tool-input-available", "toolCallId": call_id, "toolName": TOOL_NAME, "input": args}
    output = None
    async for event in stream_workflow(args, {"state": convo.state, "upstream": settings.upstream}):
        if event["type"] == "tool-output":
            output = event["output"]
        else:
            record.stages.append(event)
            yield event
    record.tool_calls.append({"name": TOOL_NAME, "args": args, "output": output})
    yield {"type": "tool-output-available", "toolCallId": call_id, "output": output}


async def _stream_agent(agent: Agent, history, record: TurnRecord, prefix: str) -> AsyncIterator[dict[str, Any]]:
    """Run a vel agent on the conversation; forward protocol chunks, record calls."""
    pending: dict[str, dict[str, Any]] = {}
    text: list[str] = []
    async for event in agent.run_stream({"messages": list(history)}):
        kind = event.get("type", "")
        if kind == "response-metadata":
            if event.get("usage"):
                record.usage.append({"role": prefix, **event["usage"]})
            continue
        if kind == "tool-input-available":
            pending[event["toolCallId"]] = {"name": event["toolName"], "args": event.get("input") or {}}
        elif kind in ("tool-output-available", "tool-output-error"):
            call = pending.pop(event["toolCallId"], {"name": "?", "args": {}})
            call["output"] = event.get("output") if kind == "tool-output-available" else {"status": "error", "error": event.get("errorText")}
            record.tool_calls.append(call)
        elif kind == "text-delta":
            text.append(event.get("delta", ""))
        elif kind == "data-workflow-stage":
            record.stages.append(event)
        if kind in FORWARD or kind.startswith("data-"):
            chunk = {k: v for k, v in event.items() if k not in ("metadata", "providerMetadata")}
            if "id" in chunk and kind.split("-")[0] in ("text", "reasoning"):
                chunk["id"] = f"{prefix}:{chunk['id']}"
            yield chunk
    record.text = "".join(text).strip()


def _instructed(convo: Conversation, gate: GateDecision) -> str:
    instruction = prompts.TURN_INSTRUCTIONS[gate.action].format(missing=", ".join(gate.missing))
    return prompts.BASE + prompts.context_block(convo.state) + "\n\n# This turn\n" + instruction


def _agent(settings, role, system, *, tools: bool, convo: Conversation, tool_choice=None) -> Agent:
    generation_config: dict[str, Any] = {"temperature": 0}
    if tools:
        generation_config["parallel_tool_calls"] = False
        if tool_choice:
            generation_config["tool_choice"] = tool_choice
    factory = settings.agent_factory or _default_agent
    return factory(
        role=role, settings=settings, system=system,
        tools=[make_tool()] if tools else [],
        tool_context={"state": convo.state, "upstream": settings.upstream},
        generation_config=generation_config,
    )


def _default_agent(*, role, settings, system, tools, tool_context, generation_config) -> Agent:
    if role == "router":
        return make_router(settings.router_model)
    return Agent(
        id=role,
        model={"provider": "openai", "model": settings.model},
        system_prompt=system,
        tools=tools,
        tool_context=tool_context,
        generation_config=generation_config,
        policies={"max_steps": 4},
    )
