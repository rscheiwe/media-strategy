"""
chat.router — the streaming chat endpoint.

POST /app/chat — run the Vel agent and stream the AI SDK UI message stream (v1) as SSE.

Persistence contract:
- The last user message is saved before streaming (inside the request DB scope).
- The assembled assistant message (text, tool and reasoning parts) is saved after the stream
  completes, inside its own `request_transaction_scope()` — the request scope has already
  closed by then (see routes/context.py).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool

from chat_api.storage.scope import request_transaction_scope
from chat_api.storage.models.usage import UsageEvent
from chat_api.storage.services.chat import title_from_message

from .agent import create_chat_agent, model_config, workflow_model_config
from .agents import DEFAULT_CHAT_MODEL, get_chat_agent_config, reasoning_model
from .trigger import WorkflowRouter
from .turns import TurnRunner

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ChatStreamMessage(BaseModel):
    role: str       # "user" | "assistant"
    content: str
    parts: list[dict[str, Any]] | None = None


class ChatStreamRequest(BaseModel):
    # Caller selects an agent by UUID; the server resolves prompt/tools/model.
    agent_uuid: str | None = None
    thinking_enabled: bool = False
    thinking_effort: str | None = None
    chat_id: str | None = None
    # Where the user is chatting from (the floating messenger sends it), e.g.
    # {"page": "home", "filters": {...}}. A hint for the model, never authorization.
    page_context: dict[str, Any] | None = None
    # One-request side channel, never sent to the model: e.g. a structured form submission
    # {"type": "tool_form_submission", "target_tool", "form_id", "parameters"}.
    metadata: dict[str, Any] | None = None
    # The browser's IANA time zone (e.g. "America/New_York"), for the date/time in the prompt.
    timezone: str | None = None
    messages: list[ChatStreamMessage]


def _lazy_explainer(api_key: str):
    """The workflow's rationale writer, built only when a run reaches the explain step.

    Any failure (no key, provider error) falls back to the templated rationale inside the DAG.
    """

    async def explain(rec):
        from chat_api.workflow.explain import vel_explainer

        return await vel_explainer(workflow_model_config(api_key))(rec)

    return explain


def _normalize_ui_stream_event(event: dict[str, Any]) -> dict[str, Any]:
    """Adapt Vel stream events to the AI SDK UI message stream shape."""
    normalized = dict(event)
    if "tool_call_id" in normalized and "toolCallId" not in normalized:
        normalized["toolCallId"] = normalized.pop("tool_call_id")
    if "tool_name" in normalized and "toolName" not in normalized:
        normalized["toolName"] = normalized.pop("tool_name")
    if normalized.get("type") == "tool-input-delta" and "delta" in normalized and "inputTextDelta" not in normalized:
        normalized["inputTextDelta"] = normalized.pop("delta")
    return normalized


# Indirection so tests can swap in a fake agent without touching Vel.
AgentFactory = Callable[..., Any]


# Vel's per-step usage keys (providers/translators.py) → usage_events columns.
_USAGE_KEYS = {
    "inputTokens": "input_tokens",
    "outputTokens": "output_tokens",
    "reasoningTokens": "reasoning_tokens",
    "cachedInputTokens": "cached_input_tokens",
    "totalTokens": "total_tokens",
}


def create_chat_router(
    *,
    chat_service,
    api_key: str,
    require_auth: Callable,
    agent_factory: AgentFactory = create_chat_agent,
    settings_repo=None,
    usage_repo=None,
    workflow_state=None,
) -> APIRouter:
    router = APIRouter(prefix="/app", tags=["chat"])

    @router.post("/chat")
    async def chat_stream(
        request: ChatStreamRequest,
        user: dict = Depends(require_auth),
    ) -> StreamingResponse:
        agent_config = get_chat_agent_config(request.agent_uuid)
        user_id = user["id"]

        if not request.messages:
            raise HTTPException(status_code=422, detail="messages must not be empty")

        # Blocking DB work is offloaded to a threadpool so a slow call degrades to one slow
        # request instead of freezing the event loop. The request DB scope is a ContextVar
        # holding a shared dict, which copy_context() propagates into the worker thread.
        chat = None
        created_chat = False
        if request.chat_id:
            chat = await run_in_threadpool(chat_service.get_chat, chat_id=request.chat_id, user_id=user_id)
            if chat is None:
                raise HTTPException(status_code=404, detail="Chat not found")
        else:
            last_user_message = next((m.content for m in reversed(request.messages) if m.role == "user"), "")
            chat = await run_in_threadpool(
                chat_service.create_chat,
                user_id=user_id,
                title=title_from_message(last_user_message),
                agent_uuid=agent_config.uuid,
            )
            created_chat = True

        last_user_entry = next((m for m in reversed(request.messages) if m.role == "user"), None)
        last_user_message = last_user_entry.content if last_user_entry is not None else ""
        if not created_chat and last_user_message and chat.title in {"", "New chat"}:
            # The UI pre-creates chats before the first send; name them after that message.
            chat = await run_in_threadpool(
                chat_service.update_chat,
                chat_id=chat.id,
                user_id=user_id,
                title=title_from_message(last_user_message),
            ) or chat
        if last_user_message:
            await run_in_threadpool(
                chat_service.append_message,
                chat_id=chat.id,
                user_id=user_id,
                role="user",
                content=last_user_message,
                parts_json=last_user_entry.parts or [{"type": "text", "text": last_user_message}],
                metadata_json={
                    "agentUuid": agent_config.uuid,
                    # Kept so a reload can show the submitted form read-only.
                    **(
                        {"formSubmission": {k: request.metadata.get(k) for k in ("form_id", "parameters")}}
                        if (request.metadata or {}).get("type") == "tool_form_submission"
                        else {}
                    ),
                },
            )

        user_settings = (
            await run_in_threadpool(settings_repo.get_or_default, user_id) if settings_repo is not None else None
        )

        # Pre-assigned so the id the client sees on the stream's `start` event is the id of the
        # saved row — feedback on a just-streamed message then targets a real message.
        assistant_message_id = str(uuid.uuid4())

        # Vel gets plain role/content; UI `parts` are for rendering and persistence only.
        vel_messages = [{"role": m.role, "content": m.content} for m in request.messages]

        observability_context = {
            "user_id": str(user_id),
            "session_id": chat.id,
            "trace_name": f"{agent_config.id}:chat",
            "tags": ["chat", agent_config.id, f"agent:{agent_config.uuid}"],
            "metadata": {
                "chat_id": chat.id,
                "agent_uuid": agent_config.uuid,
                "agent_id": agent_config.id,
                "model": agent_config.model.get("model"),
                "thinking_enabled": bool(request.thinking_enabled),
                "thinking_effort": request.thinking_effort,
                "is_new_chat": created_chat,
            },
        }
        # Thinking prefers the natively-reasoning model. `reasoning_model()` returns None when
        # that is not configured, and then `native_reasoning` stays False and the reflection
        # loop below runs instead.
        reflection_config = agent_config.reflection or {}
        requested_effort = (
            request.thinking_effort
            or str(reflection_config.get("default_effort") or "high")
        )
        try:
            native_reasoning = bool(request.thinking_enabled) and reasoning_model() is not None
        except ValueError:
            # Misconfigured reasoning model: surface it from inside the stream (below).
            native_reasoning = True

        # Recorded on usage_events when the provider does not report its model id.
        try:
            configured_model = (
                (reasoning_model() or {}).get("model") if native_reasoning else model_config().get("model")
            )
        except ValueError:
            configured_model = None

        def build_agent():
            # Built inside the stream, not here: a configuration error (missing API key,
            # half-configured reasoning model) then reaches the user as an in-chat `error`
            # event instead of a bare 500, which browsers report as an opaque CORS failure.
            return agent_factory(
                api_key=api_key,
                agent_uuid=agent_config.uuid,
                user_id=user_id,
                observability_context=observability_context,
                reasoning_effort=requested_effort if native_reasoning else None,
                custom_instructions=user_settings.custom_instructions if user_settings else None,
                page_context=request.page_context,
                timezone=request.timezone,
            )


        async def event_generator():
            assistant_parts: list[str] = []
            persisted_parts: list[dict[str, Any]] = []
            text_buffer: list[str] = []
            tool_parts_by_id: dict[str, dict[str, Any]] = {}
            data_parts_by_id: dict[str, dict[str, Any]] = {}
            error_sent = False
            # The API owns the stream framing: exactly one `start` (carrying the persisted
            # assistant id) and one `finish`, whether or not the agent emits its own.
            start_sent = False
            finish_sent = False
            reasoning_parts_by_id: dict[str, dict[str, Any]] = {}
            fallback_reasoning_id: str | None = None

            def flush_text_part() -> None:
                if not text_buffer:
                    return
                text = "".join(text_buffer)
                text_buffer.clear()
                if text:
                    persisted_parts.append({"type": "text", "text": text})

            def upsert_tool_part(event: dict[str, Any]) -> None:
                tool_call_id = event.get("toolCallId")
                if not tool_call_id:
                    return
                tool_name = event.get("toolName")
                existing = tool_parts_by_id.get(tool_call_id)
                if existing is None:
                    existing = {
                        "type": f"tool-{tool_name or 'unknown'}",
                        "toolCallId": tool_call_id,
                        "state": "input-streaming",
                    }
                    tool_parts_by_id[tool_call_id] = existing
                    persisted_parts.append(existing)
                if tool_name:
                    existing["type"] = f"tool-{tool_name}"
                event_type = event.get("type")
                if event_type == "tool-input-start":
                    existing["state"] = "input-streaming"
                elif event_type == "tool-input-delta":
                    existing["state"] = "input-streaming"
                elif event_type == "tool-input-available":
                    existing["state"] = "input-available"
                    existing["input"] = event.get("input")
                elif event_type == "tool-output-available":
                    existing["state"] = "output-available"
                    existing["output"] = event.get("output")
                elif event_type in {"tool-input-error", "tool-output-error"}:
                    existing["state"] = "output-error"
                    existing["errorText"] = event.get("errorText") or event.get("message")
                    if "input" in event:
                        existing["input"] = event.get("input")
                elif event_type == "tool-output-denied":
                    existing["state"] = "output-denied"

            def upsert_data_part(event: dict[str, Any]) -> None:
                """Persist non-transient data-* parts (e.g. workflow stage rows) as
                UIMessage data parts; a repeated id updates the part in place, as the
                AI SDK does on the client."""
                if event.get("transient"):
                    return
                part_id = event.get("id")
                existing = data_parts_by_id.get(part_id) if part_id else None
                if existing is not None:
                    existing["data"] = event.get("data")
                    return
                flush_text_part()
                part: dict[str, Any] = {"type": event["type"], "data": event.get("data")}
                if part_id:
                    part["id"] = part_id
                    data_parts_by_id[part_id] = part
                persisted_parts.append(part)

            def upsert_reasoning_part(event: dict[str, Any]) -> None:
                nonlocal fallback_reasoning_id
                event_type = event.get("type")
                reasoning_id = event.get("id")
                if not reasoning_id:
                    if fallback_reasoning_id is None:
                        fallback_reasoning_id = "reasoning"
                    reasoning_id = fallback_reasoning_id
                existing = reasoning_parts_by_id.get(reasoning_id)
                if existing is None:
                    flush_text_part()
                    existing = {
                        "type": "reasoning",
                        "text": "",
                        "state": "streaming",
                    }
                    reasoning_parts_by_id[reasoning_id] = existing
                    persisted_parts.append(existing)
                if event_type == "reasoning-delta":
                    existing["text"] = f"{existing.get('text', '')}{event.get('delta') or ''}"
                elif event_type == "reasoning-end":
                    existing["state"] = "done"
                    # RELEASE the id so the next block opens a NEW part.
                    #
                    # A turn thinks, calls a tool, thinks again — and providers
                    # reuse the same reasoning id ("0") for every one of those
                    # blocks. Keeping the id mapped meant the second block's text
                    # was appended to the FIRST part, which sits before any tool
                    # call, so a reload showed all the thinking bunched at the top
                    # and all the tools after it. The live stream interleaved them
                    # correctly; only the saved copy was wrong, which is why this
                    # only appeared after a refresh.
                    reasoning_parts_by_id.pop(reasoning_id, None)

            saved = False
            started_at = time.monotonic()
            usage_totals = {column: 0 for column in _USAGE_KEYS.values()}
            usage_steps = 0
            usage_model: str | None = None

            def record_usage(event: dict[str, Any]) -> None:
                nonlocal usage_steps, usage_model
                usage = event.get("usage") or {}
                if usage:
                    usage_steps += 1
                    for key, column in _USAGE_KEYS.items():
                        usage_totals[column] += int(usage.get(key) or 0)
                usage_model = event.get("modelId") or usage_model

            def persist_assistant_message(*, finished: bool, error: str | None = None) -> None:
                nonlocal saved
                if saved:
                    return
                saved = True
                assistant_text = "".join(assistant_parts).strip()
                has_message = bool(assistant_text or persisted_parts)
                if not has_message and usage_steps == 0:
                    return
                message_metadata: dict[str, Any] = {"agentUuid": agent_config.uuid}
                if not finished:
                    message_metadata["stopped"] = True
                if error:
                    message_metadata["error"] = error
                # This runs inside the streaming response body — AFTER the request's DB scope
                # has already closed (BaseHTTPMiddleware returns once the StreamingResponse
                # object is returned, before the generator streams) — so it MUST open its own
                # managed scope; otherwise the connection it checks out is never committed or
                # returned and sits idle-in-transaction holding the `chats` row lock.
                try:
                    with request_transaction_scope():
                        if has_message:
                            chat_service.append_message(
                                chat_id=chat.id,
                                user_id=user_id,
                                role="assistant",
                                content=assistant_text,
                                parts_json=persisted_parts or [{"type": "text", "text": assistant_text}],
                                metadata_json=message_metadata,
                                message_id=assistant_message_id,
                            )
                        if usage_repo is not None:
                            usage_repo.record(
                                UsageEvent(
                                    id=str(uuid.uuid4()),
                                    user_id=user_id,
                                    chat_id=chat.id,
                                    message_id=assistant_message_id if has_message else None,
                                    agent_uuid=agent_config.uuid,
                                    model=usage_model or configured_model,
                                    thinking=bool(request.thinking_enabled),
                                    status="error" if error else ("completed" if finished else "stopped"),
                                    steps=usage_steps,
                                    duration_ms=int((time.monotonic() - started_at) * 1000),
                                    **usage_totals,
                                )
                            )
                except Exception:
                    logger.exception("Failed to persist assistant message (chat_id=%s)", chat.id)

            try:
                if created_chat:
                    metadata_event = {
                        "type": "data-chat-metadata",
                        "data": {
                            "chatId": chat.id,
                            "chatTitle": chat.title,
                            "agentUuid": chat.agent_uuid,
                        },
                    }
                    yield f"data: {json.dumps(metadata_event)}\n\n"
                # When the model reasons natively there is nothing to bolt on —
                # and running reflection as well would produce two independent
                # sources of `reasoning-*` for one turn.
                thinking_config = None
                if (
                    not native_reasoning
                    and reflection_config.get("enabled")
                    and request.thinking_enabled
                ):
                    thinking_config = {
                        "mode": "reflection",
                        "routing": "auto",
                        "effort": requested_effort,
                        "router_model": {
                            "provider": "openai",
                            "model": str(reflection_config.get("router_model") or DEFAULT_CHAT_MODEL),
                        },
                        "thinking_model": {
                            "provider": "openai",
                            "model": str(
                                reflection_config.get("reflection_model")
                                or reflection_config.get("router_model")
                                or DEFAULT_CHAT_MODEL
                            ),
                        },
                        "emit_summaries_only": True,
                    }

                agent = TurnRunner(
                    build_agent,
                    trigger=agent_config.workflow_trigger,
                    chat_id=chat.id,
                    metadata=request.metadata,
                    workflow_state=workflow_state,
                    explainer=_lazy_explainer(api_key),
                    user_text="\n".join(m.content for m in request.messages if m.role == "user"),
                    turn=sum(1 for m in request.messages if m.role == "user"),
                    router=WorkflowRouter() if agent_config.workflow_trigger == "router" else None,
                )
                async for event in agent.run_stream(
                    {"messages": vel_messages},
                    stateless=True,
                    thinking=thinking_config,
                    observability_context=observability_context,
                ):
                    if isinstance(event, dict) and event.get("type") == "response-metadata":
                        record_usage(event)
                        continue
                    if not isinstance(event, dict):
                        continue
                    if event.get("type") == "start":
                        if start_sent:
                            continue
                        start_sent = True
                        event = {**event, "messageId": assistant_message_id}
                    elif not start_sent:
                        start_sent = True
                        yield f"data: {json.dumps({'type': 'start', 'messageId': assistant_message_id})}\n\n"
                    if event.get("type") == "finish":
                        if finish_sent:
                            continue
                        finish_sent = True
                    if isinstance(event, dict):
                        event = _normalize_ui_stream_event(event)
                        if event.get("type") == "text-delta":
                            delta = str(event.get("delta") or "")
                            assistant_parts.append(delta)
                            text_buffer.append(delta)
                        elif str(event.get("type") or "").startswith("reasoning-"):
                            upsert_reasoning_part(event)
                        elif str(event.get("type") or "").startswith("tool-"):
                            flush_text_part()
                            upsert_tool_part(event)
                        elif str(event.get("type") or "").startswith("data-"):
                            upsert_data_part(event)
                        elif event.get("type") == "error":
                            error_sent = True
                        yield f"data: {json.dumps(event)}\n\n"
                if not start_sent:
                    yield f"data: {json.dumps({'type': 'start', 'messageId': assistant_message_id})}\n\n"
                if not finish_sent:
                    yield f"data: {json.dumps({'type': 'finish'})}\n\n"
                flush_text_part()
                persist_assistant_message(finished=True)
            except (asyncio.CancelledError, GeneratorExit):
                # The client went away (Stop button, closed tab). Keep what was produced so far
                # so a reload shows the partial answer, then let the cancellation propagate —
                # no further yields are allowed on this path.
                flush_text_part()
                persist_assistant_message(finished=False)
                raise
            except Exception as exc:
                logger.exception("Chat stream failed (chat_id=%s)", chat.id)
                # Keep whatever was already streamed so a reload matches what the user saw.
                flush_text_part()
                persist_assistant_message(finished=False, error=str(exc))
                # vel streams a provider failure as an `error` event and then raises it;
                # send the client one error, not two.
                if not error_sent:
                    error_event: dict[str, Any] = {"type": "error", "errorText": str(exc)}
                    yield f"data: {json.dumps(error_event)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-transform",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
                "x-vercel-ai-ui-message-stream": "v1",
            },
        )

    return router
