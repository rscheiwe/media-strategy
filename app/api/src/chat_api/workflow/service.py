"""Runs the recommendation DAG and turns it into UI stream events.

`run_workflow` is an async generator. It yields, in order:
- `data-workflow-stage` rows (id `wf-{run_id}-{stage}`): running, then done/skipped/error;
- `data-artifact-recommendation` snapshots (id `artifact-rec-{run_id}`): loading when the
  computation starts, streaming once the plan exists, complete with the final Recommendation;
- one final `{"type": "workflow-result", ...}` for the calling tool (never sent to the client).

Server-side state (benchmarks, rejections, the explainer) stays in mesh state and never goes
on the wire; only stage summaries and the Recommendation do.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from mesh import ExecutionContext, Executor, MemoryBackend
from mesh.core.events import EventType

from chat_api.casedata import connect_readonly, ensure_case_db

from .dag import STAGE_LABELS, STAGES, build_dag
from .domain import Recommendation, Slots
from .explain import Explainer
from .policy import InventoryPolicy, SelectionPolicy, inventory_policy, selection_policy


def _now_ms() -> int:
    return int(time.time() * 1000)


def _stage(run_id: str, stage: str, status: str, summary: str = "", ms: int | None = None) -> dict:
    return {
        "type": "data-workflow-stage",
        "id": f"wf-{run_id}-{stage}",
        "data": {"run_id": run_id, "stage": stage, "label": STAGE_LABELS[stage], "status": status, "summary": summary, "ms": ms},
    }


def _artifact(run_id: str, status: str, title: str, payload: dict | None, version: int, created: int) -> dict:
    artifact_id = f"artifact-rec-{run_id}"
    return {
        "type": "data-artifact-recommendation",
        "id": artifact_id,
        "data": {
            "id": artifact_id,
            "type": "recommendation",
            "status": status,
            "version": version,
            "title": title,
            "payload": payload,
            "createdAt": created,
            "updatedAt": _now_ms(),
        },
    }


def artifact_title(slots: Slots) -> str:
    parts = [slots.client_name, slots.vertical, slots.geo]
    return " · ".join(p for p in parts if p)


async def run_workflow(
    slots: Slots,
    *,
    run_id: str | None = None,
    unconfirmed: list[str] | None = None,
    confirm: list[str] | None = None,
    explainer: Explainer | None = None,
    inv: InventoryPolicy | None = None,
    sel: SelectionPolicy | None = None,
) -> AsyncIterator[dict[str, Any]]:
    run_id = run_id or uuid.uuid4().hex[:12]
    inv = inv or inventory_policy()
    sel = sel or selection_policy()
    conn = connect_readonly(ensure_case_db())
    graph = build_dag(conn, inv, sel, explainer, form_id=f"form-{run_id}")
    context = ExecutionContext(
        graph_id="recommendation",
        session_id=f"wf-{run_id}",
        chat_history=[],
        variables={},
        # confirm: show the pre-filled form even when nothing is missing (details carried over
        # from earlier turns, or a change asked for without a value); its items are highlighted.
        state={"slots": slots.model_dump(), "unconfirmed": list(unconfirmed or []), "confirm": confirm},
    )
    created = _now_ms()
    title = artifact_title(slots)
    started: dict[str, float] = {}
    errored: set[str] = set()
    artifact_open = False
    version = 0
    try:
        async for event in Executor(graph, MemoryBackend()).execute({"run_id": run_id}, context):
            node = event.node_id
            if node not in STAGES:
                continue
            if event.type == EventType.NODE_START:
                started[node] = time.monotonic()
                if node == "benchmarks" and not artifact_open:
                    artifact_open, version = True, version + 1
                    yield _artifact(run_id, "loading", title, None, version, created)
                yield _stage(run_id, node, "running")
            elif event.type == EventType.NODE_COMPLETE:
                ms = int((time.monotonic() - started.get(node, time.monotonic())) * 1000)
                summary = (event.output or {}).get("summary", "") if isinstance(event.output, dict) else ""
                yield _stage(run_id, node, "done", summary, ms)
                if node == "alternatives":
                    version += 1
                    rec: Recommendation = context.state["recommendation"]
                    yield _artifact(run_id, "streaming", title, rec.model_dump(mode="json"), version, created)
            elif event.type == EventType.NODE_ERROR and node not in errored:
                errored.add(node)  # mesh reports a failing node more than once
                yield _stage(run_id, node, "error", "This step failed")
    except Exception as exc:  # noqa: BLE001 - a failed node ends the run cleanly
        if artifact_open:
            version += 1
            yield _artifact(run_id, "error", title, None, version, created)
        yield {"type": "workflow-result", "status": "failed", "run_id": run_id, "error": f"{type(exc).__name__}: {exc}"}
        return
    finally:
        conn.close()

    state = context.state
    if state.get("form"):
        yield {"type": "workflow-result", "status": "FORM_REQUIRED", "run_id": run_id, **state["form"]}
        return
    rec = state["recommendation"]
    version += 1
    yield _artifact(run_id, "complete", title, rec.model_dump(mode="json"), version, created)
    yield {"type": "workflow-result", "status": "done", "run_id": run_id, "artifact_id": f"artifact-rec-{run_id}", "recommendation": rec}
