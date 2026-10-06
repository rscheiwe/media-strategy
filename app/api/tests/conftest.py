from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from chat_api.app import create_app


DEFAULT_USER = "00000000-0000-0000-0000-000000000001"
USER_A = "00000000-0000-0000-0000-000000000101"
USER_B = "00000000-0000-0000-0000-000000000202"


class FakeAgent:
    """Deterministic stand-in for the provider boundary."""

    def __init__(self, events: list[dict[str, Any] | BaseException]):
        self.events = events

    async def run_stream(self, _input, *, stateless, thinking, observability_context):
        assert stateless is True
        for event in self.events:
            if isinstance(event, BaseException):
                raise event
            yield event


@pytest.fixture
def auth_headers() -> dict[str, str]:
    return {"X-User-Id": USER_A}


@pytest.fixture
def stream_events() -> list[dict[str, Any]]:
    return [
        {"type": "text-delta", "delta": "Hello "},
        {"type": "tool-input-start", "tool_call_id": "call-1", "tool_name": "list_products"},
        {"type": "tool-input-available", "tool_call_id": "call-1", "tool_name": "list_products", "input": {"query": "laptop"}},
        {"type": "tool-output-available", "tool_call_id": "call-1", "tool_name": "list_products", "output": {"products": []}},
        {"type": "text-delta", "delta": "world."},
    ]


@pytest.fixture
def client(stream_events):
    with TestClient(create_app(agent_factory=lambda **_kwargs: FakeAgent(stream_events))) as test_client:
        yield test_client


def stream_request(client: TestClient, headers: dict[str, str], **overrides):
    body = {
        "chat_id": None,
        "agent_uuid": "assistant",
        "thinking_enabled": False,
        "thinking_effort": "high",
        "messages": [{"role": "user", "content": "Hello", "parts": [{"type": "text", "text": "Hello"}]}],
    }
    body.update(overrides)
    return client.post("/app/chat", headers=headers, json=body)


def sse_frames(response) -> list[str]:
    return [line.removeprefix("data: ") for line in response.text.splitlines() if line.startswith("data: ")]
