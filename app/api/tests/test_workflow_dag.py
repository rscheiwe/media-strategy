"""Offline mesh reliability tests."""

from __future__ import annotations

import asyncio
import json

import pytest

from fastapi.testclient import TestClient
from mesh import ExecutionContext, Executor, SQLiteBackend, StateGraph
from mesh.core.events import EventType
from mesh.interrupts import InterruptResume

from chat_api.app import create_app
from chat_api.chat.trigger import RouterDecision, WorkflowRouter
from chat_api.chat.workflow_tool import run_recommendation
from chat_api.storage.container import StorageContainer
from chat_api.storage.db.sqlite import SqliteDatabaseClient
from chat_api.workflow import steps
from chat_api.workflow.domain import Slots
from chat_api.workflow.service import run_workflow


async def _events(slots: Slots, run_id: str, explainer=None):
    return [event async for event in run_workflow(slots, run_id=run_id, explainer=explainer)]


def test_dag_reliability_suite(tmp_path, monkeypatch):
    """Branch finality, clean wire events, isolation, fallback, and resume."""
    async def exercise():
        complete = Slots(client_name="Acme Shoes", vertical="Retail", kpi="ctr", geo="US", budget=30_000)
        good = await _events(complete, "wf-good")
        stages = [event for event in good if event["type"] == "data-workflow-stage"]
        assert {event["data"]["stage"] for event in stages} == {"validate", "benchmarks", "select", "scale", "viability", "plan", "alternatives", "explain"}
        assert all(event["data"]["status"] in {"running", "done", "error", "skipped"} for event in stages)
        assert all("recommendation" not in event.get("data", {}) for event in stages)
        artifacts = [event for event in good if event["type"] == "data-artifact-recommendation"]
        assert artifacts[-1]["data"]["status"] == "complete"
        assert next(event for event in good if event["type"] == "workflow-result")["status"] == "done"

        missing = await _events(Slots(geo="US", budget=25_000), "wf-missing")
        missing_stages = [event["data"]["stage"] for event in missing if event["type"] == "data-workflow-stage"]
        # The form is an internal terminal node; validate is the only streamed
        # stage on the clarification branch, and it must reach its final status.
        assert missing_stages == ["validate", "validate"]
        assert next(event for event in missing if event["type"] == "workflow-result")["status"] == "FORM_REQUIRED"

        async def broken_explainer(_rec):
            raise RuntimeError("provider unavailable")

        fallback = await _events(complete, "wf-fallback", broken_explainer)
        rec = next(event["recommendation"] for event in fallback if event["type"] == "workflow-result")
        assert rec.rationale.source == "template"
        assert rec.rationale.text

        parallel = await asyncio.gather(*[_events(Slots(client_name=f"Client {i}", vertical="Retail", kpi="ctr", geo="US", budget=30_000 + i), f"parallel-{i}") for i in range(20)])
        results = [next(event["recommendation"] for event in events if event["type"] == "workflow-result") for events in parallel]
        assert [result.requirements.client_name for result in results] == [f"Client {i}" for i in range(20)]
        assert len({result.requirements.budget for result in results}) == 20

        # Documented mesh alternative: a new Executor resumes a persisted interrupt.
        graph = StateGraph()
        graph.add_node("first", lambda state: {"value": 1}, node_type="tool")
        graph.add_node("second", lambda value: {"value": value + 1}, node_type="tool")
        graph.set_entry_point("first")
        graph.add_edge("first", "second")
        graph.set_interrupt_before("second")
        compiled = graph.compile()
        context = ExecutionContext(graph_id="resume-test", session_id="resume-session", state={})
        backend = SQLiteBackend(str(tmp_path / "mesh-resume.db"))
        first_events = [event async for event in Executor(compiled, backend).execute({}, context)]
        assert any(event.type == EventType.INTERRUPT for event in first_events)
        resumed = [event async for event in Executor(compiled, SQLiteBackend(str(tmp_path / "mesh-resume.db"))).resume_from_interrupt(context, InterruptResume())]
        assert any(event.type == EventType.EXECUTION_COMPLETE for event in resumed)

        # A predicate error must surface; neither branch is allowed to run silently.
        bad_graph = StateGraph()
        bad_graph.add_node("validate", lambda: {}, node_type="tool")
        bad_graph.add_node("left", lambda: {"ran": "left"}, node_type="tool")
        bad_graph.add_node("right", lambda: {"ran": "right"}, node_type="tool")
        bad_graph.set_entry_point("validate")
        bad_graph.add_conditional_edges("validate", lambda _out: (_ for _ in ()).throw(RuntimeError("bad predicate")), {"left": "left", "right": "right"})
        with pytest.raises(Exception, match="bad predicate"):
            [event async for event in Executor(bad_graph.compile()).execute({}, ExecutionContext(graph_id="bad", session_id="bad"))]

        # A failing real node emits one final error stage and no later completed stage.
        original_select = steps.select_products
        steps.select_products = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("select boom"))
        try:
            failed = await _events(complete, "wf-node-failure")
        finally:
            steps.select_products = original_select
        select_errors = [event for event in failed if event.get("type") == "data-workflow-stage" and event["data"]["stage"] == "select" and event["data"]["status"] == "error"]
        assert len(select_errors) == 1
        assert next(event for event in failed if event["type"] == "workflow-result")["status"] == "failed"
        assert not any(event.get("type") == "data-workflow-stage" and event["data"]["stage"] in {"scale", "viability", "plan", "alternatives", "explain"} and event["data"]["status"] == "done" for event in failed)

        # The public handler reports that node failure and always clears its lock.
        failed_storage = StorageContainer.from_db(SqliteDatabaseClient(str(tmp_path / "handler-failure.db")))
        failed_chat = failed_storage.chats.create_chat(user_id="u", title="failure", agent_uuid="assistant").id
        steps.select_products = lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("select boom"))
        try:
            handler_events = [event async for event in run_recommendation(
                {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000},
                {"chat_id": failed_chat, "workflow_state": failed_storage.workflow_state,
                 "user_text": "Retail US $30k CTR", "turn": 1, "slot_source": "llm"},
            )]
        finally:
            steps.select_products = original_select
        assert next(event["output"] for event in handler_events if event["type"] == "tool-output")["status"] == "failed"
        assert failed_storage.workflow_state.get(failed_chat).running_since is None

        # Closing a live handler generator releases its per-chat workflow lock.
        storage = StorageContainer.from_db(SqliteDatabaseClient(str(tmp_path / "cancel.db")))
        chat_id = storage.chats.create_chat(user_id="u", title="cancel", agent_uuid="assistant").id
        async def slow(_rec):
            await asyncio.sleep(10)
            return "never"
        gen = run_recommendation({"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}, {"chat_id": chat_id, "workflow_state": storage.workflow_state, "explainer": slow, "user_text": "Retail US $30k CTR", "turn": 1, "slot_source": "llm"})
        async for event in gen:
            if event.get("type") == "data-workflow-stage" and event["data"]["stage"] == "explain" and event["data"]["status"] == "running":
                break
        await gen.aclose()
        assert storage.workflow_state.get(chat_id).running_since is None

    asyncio.run(exercise())


def test_workflow_wire_has_one_outer_start_and_finish(tmp_path, monkeypatch):
    """Form and router chat paths never nest API SSE frames."""
    storage = StorageContainer.from_db(SqliteDatabaseClient(str(tmp_path / "wire.db")))
    user = "00000000-0000-0000-0000-000000000101"

    class InnerAgent:
        instruction = ""
        tool_context = {}

        async def run_stream(self, *_args, **_kwargs):
            yield {"type": "start"}
            yield {"type": "text-start", "id": "inner"}
            yield {"type": "text-delta", "id": "inner", "delta": "Hello from the inner agent."}
            yield {"type": "text-end", "id": "inner"}
            yield {"type": "finish"}

    built = []

    def agent_factory(**_kw):
        built.append(True)
        return InnerAgent()

    with TestClient(create_app(storage=storage, agent_factory=agent_factory)) as client:
        for agent_uuid in ("assistant", "planner-router"):
            chat = storage.chats.create_chat(user_id=user, title="form", agent_uuid=agent_uuid)
            state = storage.workflow_state.get(chat.id)
            state.active_form_id, state.pending_request = f"form-{agent_uuid}", True
            storage.workflow_state.save(state)
            response = client.post("/app/chat", headers={"X-User-Id": user}, json={
                "chat_id": chat.id, "agent_uuid": agent_uuid,
                "messages": [{"role": "user", "content": "Submit", "parts": [{"type": "text", "text": "Submit"}]}],
                "metadata": {"type": "tool_form_submission", "target_tool": "run_recommendation", "form_id": f"form-{agent_uuid}", "parameters": {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}},
            })
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            assert [frame["type"] for frame in frames].count("start") == 1
            assert [frame["type"] for frame in frames].count("finish") == 1
        assert not built

        async def classify_chat(_self, _history, _context):
            return RouterDecision(intent="general_chat", evidence_quote="Tell me")

        monkeypatch.setattr(WorkflowRouter, "classify", classify_chat)
        router_chat = storage.chats.create_chat(user_id=user, title="router chat", agent_uuid="planner-router")
        response = client.post("/app/chat", headers={"X-User-Id": user}, json={
            "chat_id": router_chat.id, "agent_uuid": "planner-router",
            "messages": [{"role": "user", "content": "Tell me about CTR", "parts": [{"type": "text", "text": "Tell me about CTR"}]}],
        })
        frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
        assert [frame["type"] for frame in frames].count("start") == 1
        assert [frame["type"] for frame in frames].count("finish") == 1
        assert any(frame.get("delta") == "Hello from the inner agent." for frame in frames)
