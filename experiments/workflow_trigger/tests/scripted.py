"""Scripted vel provider and agent factory for offline tests (no API key).

Follows vel's own test pattern: a BaseProvider subclass replays canned events
and is attached via agent._custom_provider. It records the tools and
generation_config of each call so tests can check what the gate allowed.
"""

from __future__ import annotations

import json
from typing import Any

from vel import Agent
from vel.events import (
    FinishMessageEvent,
    TextDeltaEvent,
    TextEndEvent,
    TextStartEvent,
    ToolInputAvailableEvent,
)
from vel.providers import BaseProvider

from wt.router import RouterDecision


class ScriptedProvider(BaseProvider):
    name = "scripted"

    def __init__(self, script: list[list[Any]]):
        self.script = list(script)
        self.calls: list[dict[str, Any]] = []

    async def stream(self, messages, model, tools, generation_config=None):
        self.calls.append({
            "messages": [dict(m) for m in messages],
            "tools": sorted(tools or {}),
            "config": dict(generation_config or {}),
        })
        if not self.script:
            raise RuntimeError("script exhausted")
        for event in self.script.pop(0):
            yield event

    async def generate(self, *args, **kwargs):
        raise NotImplementedError


def text(t: str) -> list[Any]:
    return [TextStartEvent(block_id="b"), TextDeltaEvent(block_id="b", delta=t),
            TextEndEvent(block_id="b"), FinishMessageEvent(finish_reason="stop")]


def route(intent: str, quote: str, **slots: Any) -> list[Any]:
    return text(json.dumps({"intent": intent, "evidence_quote": quote, **slots}))


def tool_call(args: dict[str, Any], call_id: str = "c1") -> list[Any]:
    return [ToolInputAvailableEvent(tool_call_id=call_id, tool_name="run_recommendation", input=args),
            FinishMessageEvent(finish_reason="tool_calls")]


class ScriptedAgents:
    """agent_factory for Settings: one scripted provider per role."""

    def __init__(self, **scripts: list[list[Any]]):
        self.providers = {role: ScriptedProvider(script) for role, script in scripts.items()}

    def __call__(self, *, role, settings, system, tools, tool_context, generation_config) -> Agent:
        provider = self.providers[role]
        if role == "router":
            agent = Agent(id="router", model={"provider": "scripted", "model": "m"},
                          output_type=RouterDecision)
        else:
            agent = Agent(id=role, model={"provider": "scripted", "model": "m"}, system_prompt=system,
                          tools=tools, tool_context=tool_context,
                          generation_config=generation_config, policies={"max_steps": 4})
        agent._custom_provider = provider
        return agent
