"""Main API: POST /chat streams one turn as an AI SDK UI message stream.

    uv run uvicorn wt.server:app --port 8100

Body: {"session_id": "...", "message": "...", "variant": "v1" | "v2" | "v3"}.
The upstream workflow service runs in-process (ASGI transport) unless
UPSTREAM_URL points at a separately running `wt.upstream:app`.
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from wt import upstream
from wt.variants import Conversation, Settings, run_turn
from wt.workflow_tool import upstream_client_factory

load_dotenv()

STREAM_HEADERS = {
    "Cache-Control": "no-cache, no-transform",
    "X-Accel-Buffering": "no",
    "x-vercel-ai-ui-message-stream": "v1",
}


class ChatRequest(BaseModel):
    session_id: str
    message: str
    variant: str = "v3"


def default_settings() -> Settings:
    url = os.getenv("UPSTREAM_URL")
    return Settings(
        model=os.getenv("CHAT_MODEL", "gpt-4o-mini"),
        router_model=os.getenv("ROUTER_MODEL", "gpt-4o-mini"),
        upstream=upstream_client_factory(base_url=url) if url else upstream_client_factory(app=upstream.app),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    app = FastAPI(title="Workflow-trigger chat API")
    app.state.settings = settings or default_settings()
    app.state.sessions = {}

    @app.post("/chat")
    async def chat(request: ChatRequest) -> StreamingResponse:
        convo = app.state.sessions.setdefault(request.session_id, Conversation(id=request.session_id))
        stream = turn_stream(request.variant, convo, request.message, app.state.settings)
        return StreamingResponse(stream, media_type="text/event-stream", headers=STREAM_HEADERS)

    return app


async def turn_stream(variant: str, convo: Conversation, message: str, settings: Settings) -> AsyncIterator[str]:
    """SSE frames for one turn: start, the turn's chunks, finish, [DONE]."""
    yield _frame({"type": "start", "messageId": f"msg-{convo.id}-{len(convo.turns)}"})
    async for chunk in run_turn(variant, convo, message, settings):
        yield _frame(chunk)
    yield _frame({"type": "finish"})
    yield "data: [DONE]\n\n"


def _frame(chunk: dict[str, Any]) -> str:
    return f"data: {json.dumps(chunk, default=str)}\n\n"


app = create_app()
