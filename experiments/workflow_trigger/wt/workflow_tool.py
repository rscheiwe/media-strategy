"""The workflow tool: proxies the upstream workflow service's stage stream.

A vel async-generator ToolSpec. Each upstream stage event becomes a
``data-workflow-stage`` part with a stable id (``stage-<run>-<name>``), so the
frontend updates one row per stage in place; the final ``tool-output`` carries
the result summary. Arguments are validated in code before anything runs.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator
from typing import Any

import httpx
from vel import ToolSpec

from wt import pipeline

TOOL_NAME = "run_recommendation"

DESCRIPTION = """\
Run the media-recommendation workflow: benchmarks products on the client's \
vertical, checks inventory scale for the geo and budget, and returns a recommended \
product or bundle. It takes about a minute and streams its stages to the user.

Call it ONLY when the user has asked for a recommendation or media plan (directly, \
or by giving a client brief), AND vertical, KPI, geo and budget are all known from \
the conversation.

Do NOT call it when the user:
- is chatting, greeting, or thanking you;
- asks how the assistant, a product, or a metric (CPM, CTR, in-view rate) works;
- asks about a recommendation already shown (explain it from the earlier result);
- says not to run it yet, or is still deciding;
- is missing any of vertical, KPI, geo or budget (ask for exactly what is missing)."""

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "vertical": {"type": "string", "enum": list(pipeline.VERTICALS)},
        "kpi": {"type": "string", "enum": list(pipeline.KPIS)},
        "geo": {"type": "string", "enum": list(pipeline.GEOS)},
        "budget": {"type": "number", "description": "Total budget in US dollars"},
        "impression_goal": {"type": ["integer", "null"], "description": "Minimum impressions, if stated"},
        "client_name": {"type": ["string", "null"], "description": "Advertiser name, if stated"},
    },
    "required": ["vertical", "kpi", "geo", "budget", "impression_goal", "client_name"],
    "additionalProperties": False,
}


async def run_recommendation(input: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """vel tool handler. ctx: {'state': conversation state, 'upstream': client factory}."""
    async for event in stream_workflow(input, ctx):
        yield event


async def stream_workflow(args: dict[str, Any], ctx: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Validate, call the upstream service, and translate its stages.

    Shared by the LLM-called tool and direct dispatch (variant V3).
    """
    state = ctx["state"]
    slots = {k: args.get(k) for k in INPUT_SCHEMA["properties"]}
    missing = pipeline.missing_slots(slots)
    if missing:
        yield {"type": "tool-output", "output": {"status": "not_run", "missing": missing}}
        return
    if state.get("workflow_running"):
        yield {"type": "tool-output", "output": {"status": "not_run", "reason": "already running"}}
        return

    key = _idempotency_key(state.get("conversation_id", ""), slots)
    state["workflow_running"] = True
    result: dict[str, Any] | None = None
    try:
        async with ctx["upstream"]() as client:
            async with client.stream("POST", "/runs", json={**slots, "idempotency_key": key}) as response:
                response.raise_for_status()
                async for event in _sse_events(response):
                    if event["event"] == "stage":
                        yield {
                            "type": "data-workflow-stage",
                            "id": f"stage-{key[:8]}-{event['stage']}",
                            "data": {k: event.get(k) for k in ("stage", "status", "summary")},
                        }
                    elif event["event"] == "result":
                        result = event
    finally:
        state["workflow_running"] = False

    if result is None:
        yield {"type": "tool-output", "output": {"status": "failed", "reason": "no result from workflow"}}
        return
    state["last_result"] = result["result"]
    state["results_shown"] = True
    yield {"type": "tool-output", "output": {"status": "done", "run_id": result["run_id"], **result["result"]}}


def make_tool() -> ToolSpec:
    return ToolSpec(
        name=TOOL_NAME,
        description=DESCRIPTION,
        input_schema=INPUT_SCHEMA,
        output_schema={},
        handler=run_recommendation,
    )


def upstream_client_factory(base_url: str | None = None, app: Any = None):
    """httpx client factory: a real URL, or the in-process upstream app."""

    def factory() -> httpx.AsyncClient:
        if app is not None:
            return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://upstream")
        return httpx.AsyncClient(base_url=base_url, timeout=120)

    return factory


def _idempotency_key(conversation_id: str, slots: dict[str, Any]) -> str:
    raw = json.dumps({"conversation": conversation_id, **slots}, sort_keys=True)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def _sse_events(response: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    async for line in response.aiter_lines():
        if line.startswith("data:"):
            yield json.loads(line[5:].strip())
