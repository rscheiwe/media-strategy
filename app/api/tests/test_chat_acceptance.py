from __future__ import annotations

import asyncio
import csv
import json
import sys
from types import SimpleNamespace

from fastapi.testclient import TestClient

from chat_api.app import create_app
from chat_api.chat.agent import model_config, workflow_model_config
from chat_api.chat.router import _lazy_explainer
from chat_api.chat.tools import LIST_PRODUCTS, build_tools, catalog_path
from chat_api.chat.trigger import WorkflowRouter
from chat_api.storage.container import StorageContainer
from chat_api.storage.db.sqlite import SqliteDatabaseClient
from chat_api.storage.examples import seed_examples

from .conftest import FakeAgent, USER_B, sse_frames, stream_request


def test_example_chat_seeding_is_per_user_once_and_keeps_deletions(tmp_path):
    """Compact exported chats seed once per user and never resurrect."""
    fixture = [{"chat": {"id": "ex-chat-1", "title": "Example", "agent_uuid": None, "is_archived": 0, "pinned_at": None, "created_at": "2026-10-05T12:00:00+00:00", "updated_at": "2026-10-05T12:00:00+00:00"}, "messages": [{"id": "ex-msg-1", "role": "user", "content": "Acme", "parts_json": '[{"type":"text","text":"Acme"}]', "metadata_json": "{}", "display_order": 0, "created_at": "2026-10-05T12:00:00+00:00"}, {"id": "ex-msg-2", "role": "assistant", "content": "Done", "parts_json": '[{"type":"text","text":"Done"}]', "metadata_json": "{}", "display_order": 1, "created_at": "2026-10-05T12:00:01+00:00"}], "workflow_state": {"slots_json": '{"vertical":{"value":"Retail","source":"router","turn":1}}', "pending_request": 0, "asked_for_json": "[]", "active_form_id": None, "last_run_json": None, "updated_at": "2026-10-05T12:00:01+00:00"}}]
    storage = StorageContainer.from_db(SqliteDatabaseClient(str(tmp_path / "examples.db")))
    user_a, user_b = "user-a", "user-b"
    assert seed_examples(storage, user_a, fixture) == 1
    chats_a = storage.chats.list_chats(user_id=user_a, limit=20, offset=0)
    assert len(chats_a) == 1
    messages = storage.chats.list_messages(chat_id=chats_a[0].id, user_id=user_a)
    assert [m.content for m in messages] == ["Acme", "Done"]
    assert storage.workflow_state.get(chats_a[0].id).slot_values()["vertical"] == "Retail"
    assert seed_examples(storage, user_a, fixture) == 0
    storage.chats.update_chat(chat_id=chats_a[0].id, user_id=user_a, is_archived=True)
    assert seed_examples(storage, user_a, fixture) == 0
    assert storage.chats.list_chats(user_id=user_a, limit=20, offset=0) == []
    assert seed_examples(storage, user_b, fixture) == 1
    assert storage.chats.list_chats(user_id=user_b, limit=20, offset=0)[0].id != chats_a[0].id
    empty_user = "empty"
    assert seed_examples(storage, empty_user, []) == 0
    assert seed_examples(storage, empty_user, fixture) == 1


def test_example_chats_through_the_route(tmp_path, monkeypatch, auth_headers):
    """GET /app/chats seeds when APP_EXAMPLE_CHATS=1; restarts, owners and the real fixture."""
    from chat_api.storage import examples

    fixture = [{
        "chat": {"id": "ex-route-1", "title": "Example route", "agent_uuid": None, "is_archived": 0, "pinned_at": None,
                 "created_at": "2026-10-05T12:00:00+00:00", "updated_at": "2026-10-05T12:00:00+00:00"},
        "messages": [{"id": "ex-route-msg", "role": "user", "content": "Acme", "parts_json": '[{"type":"text","text":"Acme"}]',
                      "metadata_json": "{}", "display_order": 0, "created_at": "2026-10-05T12:00:00+00:00"}],
        "workflow_state": None,
    }]
    path = tmp_path / "example_chats.json"
    path.write_text(json.dumps(fixture))
    monkeypatch.setattr(examples, "EXAMPLES_PATH", path)
    monkeypatch.setattr(examples, "load_examples", lambda p=None: json.loads(path.read_text()))
    db_path = str(tmp_path / "chat.db")

    def titles(headers):
        with TestClient(create_app(db_path=db_path, agent_factory=lambda **_k: FakeAgent([]))) as c:
            return [chat["title"] for chat in c.get("/app/chats", headers=headers).json()["chats"]], c

    # Off by default: a new user's list stays empty.
    monkeypatch.delenv("APP_EXAMPLE_CHATS", raising=False)
    assert titles(auth_headers)[0] == []

    monkeypatch.setenv("APP_EXAMPLE_CHATS", "1")
    other = {**auth_headers, "X-User-Id": USER_B}
    assert titles(other)[0] == ["Example route"]
    assert titles(other)[0] == ["Example route"]  # a second listing adds none

    # Delete it, then "restart" (a new app on the same file): it stays deleted.
    with TestClient(create_app(db_path=db_path, agent_factory=lambda **_k: FakeAgent([]))) as c:
        chat_id = c.get("/app/chats", headers=other).json()["chats"][0]["id"]
        assert c.delete(f"/app/chats/{chat_id}", headers=other).status_code == 200
    assert titles(other)[0] == []

    # A user who owns the original chat id gets no copy.
    owner = "owner-of-originals"
    storage = StorageContainer.from_db(SqliteDatabaseClient(db_path))
    storage.db.execute_write(
        "INSERT INTO chats (id, user_id, title, created_at, updated_at) VALUES (%s, %s, %s, %s, %s)",
        ("ex-route-1", owner, "Original", "2026-10-05T12:00:00+00:00", "2026-10-05T12:00:00+00:00"),
    )
    assert titles({**auth_headers, "X-User-Id": owner})[0] == ["Original"]

    # The real fixture loads, and every message's parts are a JSON list.
    real = json.loads(examples.Path(examples.__file__).with_name("example_chats.json").read_text())
    assert len(real) >= 3
    for example in real:
        assert example["chat"]["id"] and example["messages"]
        for message in example["messages"]:
            assert isinstance(json.loads(message["parts_json"]), list)
        assert any(m["role"] == "assistant" for m in example["messages"])


def test_stream_is_v1_and_start_id_is_the_persisted_assistant_id(client, auth_headers):
    response = stream_request(client, auth_headers)
    assert response.status_code == 200
    assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
    frames = sse_frames(response)
    metadata = json.loads(frames[0])
    assert metadata["type"] == "data-chat-metadata"
    chat_id = metadata["data"]["chatId"]
    events = [json.loads(frame) for frame in frames[1:-1]]
    start = next(event for event in events if event["type"] == "start")
    assert [event["type"] for event in events].count("start") == 1
    assert [event["type"] for event in events].count("finish") == 1
    assert frames[-1] == "[DONE]"

    saved = client.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"]
    assert saved[-1]["id"] == start["messageId"]


def test_stream_deduplicates_agent_start_and_finish_frames(auth_headers):
    events = [{"type": "start"}, {"type": "text-delta", "delta": "Answer"}, {"type": "finish"}]
    with TestClient(create_app(agent_factory=lambda **_kwargs: FakeAgent(events))) as client:
        response = stream_request(client, auth_headers)
        frames = [json.loads(frame) for frame in sse_frames(response)[1:-1]]
        starts = [event for event in frames if event["type"] == "start"]
        assert len(starts) == 1
        assert sum(event["type"] == "finish" for event in frames) == 1
        chat_id = json.loads(sse_frames(response)[0])["data"]["chatId"]
        assert starts[0]["messageId"] == client.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"][-1]["id"]


def test_sqlite_survives_restart_and_retains_ordered_tool_parts(tmp_path, auth_headers, stream_events):
    db_path = str(tmp_path / "chat.sqlite")
    factory = lambda **_kwargs: FakeAgent(stream_events)
    with TestClient(create_app(db_path=db_path, agent_factory=factory)) as first:
        response = stream_request(first, auth_headers)
        chat_id = json.loads(sse_frames(response)[0])["data"]["chatId"]
        before = first.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"]
    with TestClient(create_app(db_path=db_path, agent_factory=factory)) as restarted:
        after = restarted.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"]
    assert [message["role"] for message in after] == ["user", "assistant"]
    assert after == before
    assert [part["type"] for part in after[-1]["parts"]] == ["text", "tool-list_products", "text"]


def test_chat_crud_orders_pins_and_isolates_users(client, auth_headers):
    first = client.post("/app/chats", headers=auth_headers, json={"title": "First"}).json()["chat"]
    second = client.post("/app/chats", headers=auth_headers, json={"title": "Second"}).json()["chat"]
    assert client.patch(f"/app/chats/{first['id']}", headers=auth_headers, json={"title": "Renamed", "pinned": True}).json()["chat"]["title"] == "Renamed"
    client.patch(f"/app/chats/{second['id']}", headers=auth_headers, json={"title": "Most recent unpinned"})
    chats = client.get("/app/chats", headers=auth_headers).json()["chats"]
    assert [chat["id"] for chat in chats] == [first["id"], second["id"]]
    other = {"X-User-Id": USER_B}
    assert client.get(f"/app/chats/{first['id']}", headers=other).status_code == 404
    assert client.get("/app/chats", headers=other).json()["chats"] == []
    assert client.delete(f"/app/chats/{first['id']}", headers=auth_headers).status_code == 200
    assert [chat["id"] for chat in client.get("/app/chats", headers=auth_headers).json()["chats"]] == [second["id"]]


def test_stream_error_keeps_partial_assistant_message(auth_headers):
    error_factory = lambda **_kwargs: FakeAgent([{"type": "text-delta", "delta": "Partial"}, RuntimeError("model failed")])
    with TestClient(create_app(agent_factory=error_factory)) as client:
        response = stream_request(client, auth_headers)
        frames = sse_frames(response)
        assert json.loads(frames[-2]) == {"type": "error", "errorText": "model failed"}
        chat_id = json.loads(frames[0])["data"]["chatId"]
        assistant = client.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"][-1]
        assert assistant["content"] == "Partial"
        assert assistant["metadata"]["error"] == "model failed"

def test_cancelled_stream_keeps_partial_assistant_message_marked_stopped(auth_headers):
    stopped_factory = lambda **_kwargs: FakeAgent([{"type": "text-delta", "delta": "Partial"}, asyncio.CancelledError()])
    with TestClient(create_app(agent_factory=stopped_factory)) as client:
        chat_id = client.post("/app/chats", headers=auth_headers, json={"title": "Stop test"}).json()["chat"]["id"]
        # TestClient may suppress an aborted response body; the pre-created chat
        # lets us still observe the server-side persistence boundary.
        stream_request(client, auth_headers, chat_id=chat_id)
        assistant = client.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"][-1]
        assert assistant["content"] == "Partial"
        assert assistant["metadata"]["stopped"] is True


def test_assistant_feedback_is_saved_replaced_and_cleared(client, auth_headers):
    response = stream_request(client, auth_headers)
    chat_id = json.loads(sse_frames(response)[0])["data"]["chatId"]
    message_id = client.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"][-1]["id"]
    assert client.put(f"/app/chats/{chat_id}/messages/{message_id}/feedback", headers=auth_headers, json={"rating": 1}).json()["feedback"] == {"rating": 1, "comment": None}
    assert client.put(f"/app/chats/{chat_id}/messages/{message_id}/feedback", headers=auth_headers, json={"rating": -1}).json()["feedback"]["rating"] == -1
    assert client.delete(f"/app/chats/{chat_id}/messages/{message_id}/feedback", headers=auth_headers).status_code == 204
    assert client.get(f"/app/chats/{chat_id}", headers=auth_headers).json()["messages"][-1]["feedback"] is None


def test_product_catalog_tool_and_optional_web_search(monkeypatch):
    product = LIST_PRODUCTS._handler({}, {})
    with catalog_path().open(newline="") as source:
        expected = [{**row, "cpm": float(row["cpm"])} for row in csv.DictReader(source)]
    assert product == {"products": expected}
    monkeypatch.delenv("LINKUP_API_KEY", raising=False)
    assert "web_search" not in {tool.name for tool in build_tools() if tool.is_enabled({})}
    monkeypatch.setenv("LINKUP_API_KEY", "test-key")
    assert "web_search" in {tool.name for tool in build_tools() if tool.is_enabled({})}


def test_provider_config_defaults_and_ollama_override(monkeypatch):
    monkeypatch.delenv("APP_CHAT_PROVIDER", raising=False)
    monkeypatch.delenv("APP_CHAT_MODEL", raising=False)
    monkeypatch.delenv("APP_WORKFLOW_MODEL", raising=False)
    monkeypatch.delenv("APP_CHAT_BASE_URL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert model_config() == {"provider": "openai", "model": "gpt-5.4"}
    assert workflow_model_config() == {"provider": "openai", "model": "gpt-5.4-mini"}

    monkeypatch.setenv("APP_WORKFLOW_MODEL", "workflow-override")
    assert workflow_model_config() == {"provider": "openai", "model": "workflow-override"}

    captured_router_models = []

    class CapturingRouterAgent:
        def __init__(self, **kwargs):
            captured_router_models.append(kwargs["model"])

        async def run_stream(self, *_args, **_kwargs):
            yield {"type": "data-object-complete", "data": {"object": {"intent": "general_chat", "evidence_quote": "hello"}}}

    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=CapturingRouterAgent))
    decision = asyncio.run(WorkflowRouter().classify([{"role": "user", "content": "hello"}], {}))
    assert decision.intent == "general_chat"
    assert captured_router_models == [workflow_model_config()]

    captured_explainer_models = []

    def fake_vel_explainer(config):
        captured_explainer_models.append(config)

        async def explain(_rec):
            return "explained"

        return explain

    monkeypatch.setattr("chat_api.workflow.explain.vel_explainer", fake_vel_explainer)
    assert asyncio.run(_lazy_explainer("test-key")("recommendation")) == "explained"
    assert captured_explainer_models == [workflow_model_config("test-key")]

    monkeypatch.delenv("APP_WORKFLOW_MODEL", raising=False)
    monkeypatch.setenv("APP_CHAT_PROVIDER", "ollama")
    monkeypatch.setenv("APP_CHAT_MODEL", "llama3.1:8b")
    monkeypatch.setenv("APP_CHAT_BASE_URL", "http://ollama.example/v1")
    assert model_config() == {"provider": "openai", "model": "llama3.1:8b", "base_url": "http://ollama.example/v1", "api_key": "ollama"}
    assert workflow_model_config() == model_config()
