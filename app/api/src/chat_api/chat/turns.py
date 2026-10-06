"""One chat turn: workflow-aware orchestration in front of the vel agent.

The router builds the vel agent as before and hands it to a TurnRunner, which exposes the same
`run_stream` the router already iterates. The runner:

- routes a form submission (`metadata.type == "tool_form_submission"`) straight to the
  workflow via `dispatch()`, with no LLM call: typed answers need no interpretation;
- for the `tool` trigger (Approach A), gives the workflow tool what it needs through the
  agent's tool_context and lets the agent decide when to call it;
- for the `router` trigger (Approach B), lets a structured router + code gate decide and
  dispatches the workflow without an LLM choosing the tool (see chat/trigger/);
- puts a state block (known details, pending request, latest recommendation) in the system
  prompt every turn, so follow-ups are grounded even though tool-only turns carry no text.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

from chat_api.workflow.domain import KPI_LABELS

from .follow_up import delivered, follow_up_events
from .workflow_tool import TOOL_NAME, dispatch

STATE_HEADER = "<recommendation_state>"


def state_block(state) -> str:
    """The per-turn summary of the workflow state, for the system prompt."""
    if state is None:
        return ""
    known = {name: entry.get("value") for name, entry in state.slots.items() if entry.get("value") not in (None, "")}
    lines = [STATE_HEADER, f"  known details: {known or 'none yet'}"]
    if state.pending_request:
        lines.append(f"  waiting for: {', '.join(state.asked_for) or 'details'} (a form was shown)")
    if state.last_run:
        s = state.last_run.get("summary") or {}
        plan = " + ".join(f"{line['name']} (${line['spend']:,.2f}, {line['impressions']:,} imps)" for line in s.get("lines", []))
        kpi = KPI_LABELS.get(known.get("kpi"), "KPI")
        blended = s.get("blended_kpi")
        lines.append(
            f"  latest recommendation (run {state.last_run.get('run_id')}): {plan or 'no viable plan'}"
            + (f"; blended {kpi} {blended:.2%}" if blended else "")
        )
        lines.append(f"  rationale: {s.get('headline', '')}")
        lines.append("  For changes to the brief, call run_recommendation again with the new detail.")
    lines.append("</recommendation_state>")
    return "\n".join(lines)


def _not_run_part(reason: str) -> list[dict[str, Any]]:
    call_id = "call-stale"
    return [
        {"type": "tool-input-start", "toolCallId": call_id, "toolName": TOOL_NAME},
        {"type": "tool-input-available", "toolCallId": call_id, "toolName": TOOL_NAME, "input": {}},
        {"type": "tool-output-available", "toolCallId": call_id, "output": {"status": "not_run", "reason": reason}},
    ]


# vel ends a run with this error when a model step returns no text and no tool call. The
# model occasionally does that on a successful call (HTTP 200, finish_reason "stop", empty
# content), so it's retried once before the user sees anything; never shown as an error.
EMPTY_REPLY_ERROR = "No response from LLM"
EMPTY_REPLY_FALLBACK = "Sorry, I didn't get a reply that time. Please try again."
_VISIBLE = ("text-delta", "tool-input-start", "tool-input-available", "tool-output-available")


async def stream_with_empty_retry(make_stream) -> AsyncIterator[dict[str, Any]]:
    """Stream an agent run; if it ends empty, run it once more (only if nothing was shown yet).

    `make_stream()` returns a fresh `agent.run_stream(...)`. The second run's `start` is
    dropped so the turn keeps one frame. If the retry is empty too, or the empty step came
    after visible output, a short text reply replaces vel's error.
    """
    started = False
    for attempt in (1, 2):
        empty = shown = False
        async for event in make_stream():
            kind = event.get("type") if isinstance(event, dict) else None
            if kind == "error" and event.get("errorText") == EMPTY_REPLY_ERROR:
                empty = True
                continue
            if empty and kind == "finish":
                continue  # held: the turn isn't over yet
            if kind == "start":
                if started:
                    continue
                started = True
            if kind in _VISIBLE:
                shown = True
            yield event
        if not empty:
            return
        if attempt == 1 and not shown:
            continue
        yield {"type": "text-start", "id": "empty-reply"}
        yield {"type": "text-delta", "id": "empty-reply", "delta": EMPTY_REPLY_FALLBACK}
        yield {"type": "text-end", "id": "empty-reply"}
        yield {"type": "finish"}
        return


class TurnRunner:
    def __init__(
        self,
        build_agent: Callable[[], Any],
        *,
        trigger: str | None,
        chat_id: str | None,
        metadata: dict[str, Any] | None,
        workflow_state=None,
        explainer=None,
        user_text: str = "",
        turn: int = 0,
        router=None,
    ) -> None:
        self._build_agent = build_agent
        self.trigger = trigger
        self.chat_id = chat_id
        self.metadata = metadata or {}
        self.repo = workflow_state
        self.explainer = explainer
        self.user_text = user_text
        self.turn = turn
        self.router = router
        self.agent = None

    def _ctx(self, source: str) -> dict[str, Any]:
        return {
            "chat_id": self.chat_id,
            "workflow_state": self.repo,
            "explainer": self.explainer,
            "user_text": self.user_text,
            "turn": self.turn,
            "slot_source": source,
        }

    def _is_form_submission(self) -> bool:
        return self.metadata.get("type") == "tool_form_submission" and self.metadata.get("target_tool") == TOOL_NAME

    async def run_stream(self, payload: dict[str, Any], **kwargs) -> AsyncIterator[dict[str, Any]]:
        if self.trigger and self._is_form_submission():
            async for event in self._form_turn():
                yield event
            return
        if self.trigger == "router" and self.router is not None:
            async for event in self.router.run(self, payload, **kwargs):
                yield event
            return
        agent = self.agent or self._build_agent()
        self.agent = agent
        if self.trigger:
            self.prepare_agent(agent)
        async for event in stream_with_empty_retry(lambda: agent.run_stream(payload, **kwargs)):
            yield event

    def prepare_agent(self, agent) -> None:
        """Add the workflow context and the state block to a built vel agent."""
        context = getattr(agent, "tool_context", None)
        if isinstance(context, dict):
            context.update(self._ctx("llm"))
        state = self.repo.get(self.chat_id) if (self.repo and self.chat_id) else None
        block = state_block(state)
        # vel sends `instruction` as an extra system message each run, after the system prompt.
        if block and hasattr(agent, "instruction") and not callable(agent.instruction):
            agent.instruction = f"{agent.instruction}\n\n{block}" if agent.instruction else block

    async def _form_turn(self) -> AsyncIterator[dict[str, Any]]:
        """A form submission: validate the form id, then run the workflow with no LLM call."""
        yield {"type": "start"}
        state = self.repo.get(self.chat_id) if (self.repo and self.chat_id) else None
        form_id = self.metadata.get("form_id")
        if state is not None and form_id and state.active_form_id != form_id:
            for event in _not_run_part("stale_form"):
                yield event
            yield {"type": "finish"}
            return
        params = dict(self.metadata.get("parameters") or {})
        # Say what's happening (a fixed sentence: a form submission makes no chat-model call).
        yield {"type": "text-start", "id": "form-submit"}
        yield {"type": "text-delta", "id": "form-submit", "delta": "Thanks, running the recommendation on those details now."}
        yield {"type": "text-end", "id": "form-submit"}
        rec = None
        async for event in dispatch(params, self._ctx("form")):
            rec = delivered(event, rec)
            yield event
        async for event in follow_up_events(rec):
            yield event
        yield {"type": "finish"}
