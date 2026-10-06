"""The "other API": a multi-stage workflow service that streams its stages.

Stands in for a separate workflow service. ``POST /runs``
takes the campaign slots and streams Server-Sent Events:

    {"event": "stage", "stage": "benchmarks", "status": "running"}
    {"event": "stage", "stage": "benchmarks", "status": "done", "summary": "..."}
    ...
    {"event": "result", "run_id": "...", "result": {...}}

Stages: benchmarks -> scale -> viability -> allocate (deterministic, real data)
-> analyst (an LLM summary when ANALYST_MODE=llm, else a template).
"""

from __future__ import annotations

import asyncio
import json
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from wt import pipeline

STAGES = ("benchmarks", "scale", "viability", "allocate", "analyst")
STAGE_DELAY_S = float(os.getenv("UPSTREAM_STAGE_DELAY_S", "0.05"))


class RunRequest(BaseModel):
    vertical: str
    kpi: str
    geo: str
    budget: float
    impression_goal: int | None = None
    client_name: str | None = None
    idempotency_key: str | None = None


app = FastAPI(title="Recommendation workflow service")
_runs: dict[str, dict[str, Any]] = {}  # idempotency key -> finished result


@app.post("/runs")
async def start_run(request: RunRequest) -> StreamingResponse:
    slots = request.model_dump()
    missing = pipeline.missing_slots(slots)
    if missing:
        raise HTTPException(status_code=422, detail={"missing": missing})
    return StreamingResponse(_sse(run_workflow(slots)), media_type="text/event-stream")


async def run_workflow(slots: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """The workflow as a stream of stage and result events."""
    key = slots.get("idempotency_key")
    if key and key in _runs:
        yield {"event": "result", **_runs[key], "replayed": True}
        return

    run_id = uuid.uuid4().hex[:10]
    data = pipeline.load_data()

    yield _stage("benchmarks", "running")
    bench = pipeline.benchmarks(slots, data)
    best = max(bench, key=lambda p: bench[p][slots["kpi"]])
    yield _stage("benchmarks", "done", f"{len(bench)} products benchmarked; {best} leads on {slots['kpi']}")

    yield _stage("scale", "running")
    scaled = pipeline.scale(slots, bench, data)
    over = [p for p, s in scaled.items() if s["affordable_imps"] > s["effective_capacity"]]
    yield _stage("scale", "done", f"{len(over)} of {len(scaled)} products can't absorb the full budget")

    yield _stage("viability", "running")
    rejected = pipeline.viability(slots, scaled)
    yield _stage("viability", "done", f"{len(rejected)} rejected" + (f": {rejected}" if rejected else ""))

    yield _stage("allocate", "running")
    allocation = pipeline.allocate(slots, bench, scaled, rejected, data)
    plan = " + ".join(f"{line['product_id']} ${line['spend']:,.0f}" for line in allocation["lines"])
    yield _stage("allocate", "done", plan or "no viable allocation")

    yield _stage("analyst", "running")
    analysis = await _analyst(slots, allocation, rejected)
    yield _stage("analyst", "done", "rationale written")

    result = {
        "run_id": run_id,
        "result": {
            "slots": {k: v for k, v in slots.items() if k != "idempotency_key"},
            "allocation": allocation,
            "rejected": rejected,
            "analysis": analysis,
        },
    }
    if key:
        _runs[key] = result
    yield {"event": "result", **result}


def _stage(name: str, status: str, summary: str | None = None) -> dict[str, Any]:
    event: dict[str, Any] = {"event": "stage", "stage": name, "status": status}
    if summary:
        event["summary"] = summary
    return event


async def _analyst(slots: dict[str, Any], allocation: dict[str, Any], rejected: dict[str, str]) -> str:
    """One LLM call when ANALYST_MODE=llm (an agentic stage); a template otherwise."""
    if os.getenv("ANALYST_MODE", "template") != "llm":
        lines = "; ".join(
            f"{line['product_name']} gets ${line['spend']:,.0f} ({line['impressions']:,} imps)"
            for line in allocation["lines"]
        )
        return f"Recommended for {slots['vertical']}/{slots['geo']} on {slots['kpi']}: {lines}."
    from vel import Agent

    agent = Agent(
        id="analyst",
        model={"provider": "openai", "model": os.getenv("ANALYST_MODEL", "gpt-4o-mini")},
        system_prompt="Explain this media plan to a strategist in 2 sentences. Use only the numbers given.",
        generation_config={"temperature": 0},
    )
    payload = json.dumps({"slots": slots, "allocation": allocation, "rejected": rejected})
    return str(await agent.run({"messages": [{"role": "user", "content": payload}]}))


async def _sse(events: AsyncIterator[dict[str, Any]]) -> AsyncIterator[str]:
    async for event in events:
        await asyncio.sleep(STAGE_DELAY_S)  # make the stages visibly sequential
        yield f"data: {json.dumps(event)}\n\n"
