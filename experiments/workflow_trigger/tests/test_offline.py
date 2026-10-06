"""Offline tests: gate, workflow proxy, the three variants, and the /chat stream."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tests.scripted import ScriptedAgents, route, text, tool_call
from wt import upstream
from wt.gate import decide
from wt.router import RouterDecision
from wt.server import create_app
from wt.variants import Conversation, Settings, run_turn
from wt.workflow_tool import TOOL_NAME, stream_workflow, upstream_client_factory

ACME = "Acme Shoes is a Retail advertiser running in the US with a $30,000 budget. They care most about click-through rate."
ACME_SLOTS = {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30000}
ACME_ARGS = {**ACME_SLOTS, "impression_goal": None, "client_name": "Acme Shoes"}
UPSTREAM = upstream_client_factory(app=upstream.app)


async def _turn(variant, convo, message, agents):
    settings = Settings(upstream=UPSTREAM, agent_factory=agents)
    chunks = [c async for c in run_turn(variant, convo, message, settings)]
    return chunks, convo.turns[-1]


# ------------------------------------------------------------------ gate

def _decision(intent, quote="x", **slots):
    return RouterDecision(intent=intent, evidence_quote=quote, **slots)


def test_gate_runs_a_complete_request():
    state = {}
    gate = decide(_decision("request_recommendation", "budget", **ACME_SLOTS), "a $30,000 budget", state)
    assert gate.action == "run"
    assert gate.args["vertical"] == "Retail"


def test_gate_asks_for_missing_details_then_runs_when_provided():
    state = {}
    first = decide(_decision("request_recommendation", "budget", geo="US", budget=25000, kpi="in_view_rate"),
                   "US with a $25,000 budget", state)
    assert (first.action, first.missing) == ("ask", ["vertical"])
    second = decide(_decision("provide_details", "Retail", vertical="Retail"), "Retail", state)
    assert second.action == "run"


def test_gate_does_not_run_details_without_a_pending_request():
    gate = decide(_decision("provide_details", "Retail", **ACME_SLOTS), "Retail", {})
    assert gate.action == "chat"


def test_gate_downgrades_a_run_without_evidence_in_the_message():
    gate = decide(_decision("request_recommendation", "run the plan", **ACME_SLOTS), "thanks!", {})
    assert gate.action == "chat"
    assert gate.notes


def test_gate_lets_an_unquoted_turn_continue_a_pending_request():
    state = {"pending_request": True, "slots": {"vertical": "Retail"}}
    gate = decide(_decision("request_recommendation", "I need a recommendation for a Retail client.",
                            kpi="ctr", geo="US", budget=30000),
                  "US, $30k, and they care about CTR.", state)
    assert gate.action == "run"


@pytest.mark.parametrize("state, intent, expected", [
    ({"workflow_running": True}, "request_recommendation", "status"),
    ({"last_result": {"x": 1}}, "discuss_results", "discuss"),
    ({}, "discuss_results", "chat"),
    ({"pending_request": True}, "decline", "chat"),
    ({}, "general_chat", "chat"),
])
def test_gate_other_actions(state, intent, expected):
    assert decide(_decision(intent, "x", **ACME_SLOTS), "x", state).action == expected


def test_gate_ignores_invalid_slot_values():
    state = {}
    gate = decide(_decision("request_recommendation", "LATAM", vertical="Retail", kpi="ctr", budget=5000),
                  "Retail in LATAM, ctr, $5k", state)
    assert (gate.action, gate.missing) == ("ask", ["geo"])


# ------------------------------------------------------------------ workflow proxy

async def test_workflow_streams_each_stage_then_the_result():
    state = {"conversation_id": "t1"}
    events = [e async for e in stream_workflow(ACME_ARGS, {"state": state, "upstream": UPSTREAM})]

    stages = [e for e in events if e["type"] == "data-workflow-stage"]
    assert [(e["data"]["stage"], e["data"]["status"]) for e in stages] == [
        (s, status) for s in upstream.STAGES for status in ("running", "done")
    ]
    assert len({e["id"] for e in stages}) == len(upstream.STAGES)  # one id per stage
    output = events[-1]["output"]
    assert output["status"] == "done"
    assert [line["product_id"] for line in output["allocation"]["lines"]] == ["P003", "P006"]
    assert state["last_result"] and state["workflow_running"] is False


async def test_workflow_refuses_missing_details_and_concurrent_runs():
    missing = [e async for e in stream_workflow({"vertical": "Retail"}, {"state": {}, "upstream": UPSTREAM})]
    assert missing[-1]["output"] == {"status": "not_run", "missing": ["kpi", "geo", "budget"]}
    busy = [e async for e in stream_workflow(ACME_ARGS, {"state": {"workflow_running": True}, "upstream": UPSTREAM})]
    assert busy[-1]["output"]["status"] == "not_run"


# ------------------------------------------------------------------ variants

async def test_v3_dispatches_the_workflow_in_code_and_narrates():
    agents = ScriptedAgents(router=[route("request_recommendation", "budget", client_name="Acme Shoes", **ACME_SLOTS)],
                            narrator=[text("Commerce Connect leads; Attention Builder takes the rest.")])
    convo = Conversation()

    chunks, record = await _turn("v3", convo, ACME, agents)

    assert record.ran_workflow and record.gate["action"] == "run"
    assert record.tool_calls[0]["args"] == ACME_ARGS
    assert {c["type"] for c in chunks} >= {"tool-input-available", "data-workflow-stage", "tool-output-available", "text-delta"}
    assert agents.providers["narrator"].calls[0]["tools"] == []  # never offered the tool


async def test_v2_offers_and_forces_the_tool_only_when_the_gate_says_run():
    agents = ScriptedAgents(
        router=[route("request_recommendation", "budget", client_name="Acme Shoes", **ACME_SLOTS),
                route("general_chat", "thanks")],
        assistant=[tool_call(ACME_ARGS), text("Done: P003 + P006."), text("You're welcome!")],
    )
    convo = Conversation()

    _, run_record = await _turn("v2", convo, ACME, agents)
    _, chat_record = await _turn("v2", convo, "thanks", agents)

    calls = agents.providers["assistant"].calls
    assert run_record.ran_workflow
    assert calls[0]["tools"] == [TOOL_NAME]
    assert calls[0]["config"]["tool_choice"] == {"type": "function", "name": TOOL_NAME}
    assert calls[1]["config"]["tool_choice"] == "auto"  # relaxed after the tool ran (vel 0.5.2)
    assert calls[2]["tools"] == [] and not chat_record.tool_calls


async def test_v2_and_v3_ask_for_missing_details_without_tools():
    for variant in ("v2", "v3"):
        agents = ScriptedAgents(
            router=[route("request_recommendation", "US, $25,000", geo="US", budget=25000, kpi="in_view_rate")],
            assistant=[text("Which vertical is the client in?")],
        )
        _, record = await _turn(variant, Conversation(), "A new app, US, $25,000, in-view.", agents)
        assert record.gate["action"] == "ask" and record.gate["missing"] == ["vertical"]
        assert agents.providers["assistant"].calls[0]["tools"] == []
        assert not record.tool_calls


async def test_v1_leaves_the_decision_to_the_model():
    agents = ScriptedAgents(assistant=[tool_call(ACME_ARGS), text("Recommended P003 + P006.")])
    convo = Conversation()

    _, record = await _turn("v1", convo, ACME, agents)

    assert record.router is None and record.ran_workflow
    call = agents.providers["assistant"].calls[0]
    assert call["tools"] == [TOOL_NAME] and call["config"].get("tool_choice", "auto") == "auto"


# ------------------------------------------------------------------ /chat stream

def test_chat_endpoint_streams_a_ui_message_stream():
    agents = ScriptedAgents(router=[route("request_recommendation", "budget", **ACME_SLOTS)],
                            narrator=[text("Done.")])
    client = TestClient(create_app(Settings(upstream=UPSTREAM, agent_factory=agents)))

    with client.stream("POST", "/chat", json={"session_id": "s1", "message": ACME, "variant": "v3"}) as response:
        assert response.headers["x-vercel-ai-ui-message-stream"] == "v1"
        frames = [line[6:] for line in response.iter_lines() if line.startswith("data: ")]

    assert frames[-1] == "[DONE]"
    chunks = [json.loads(f) for f in frames[:-1]]
    assert chunks[0]["type"] == "start" and chunks[-1]["type"] == "finish"
    assert [c["type"] for c in chunks].count("start") == 1


# ------------------------------------------------------------------ v4

async def test_v4_sends_status_first_and_uses_the_speculative_reply_for_chat():
    agents = ScriptedAgents(router=[route("discuss_results", "Why")],
                            assistant=[text("Because P003 ran out of inventory.")])
    convo = Conversation()
    convo.state["last_result"] = None

    chunks, record = await _turn("v4", convo, "Why is P003 first in that plan?", agents)

    assert chunks[0]["type"] == "data-status" and chunks[0]["transient"] is True
    assert record.speculative == "used" and record.text.startswith("Because")
    assert record.first_visible_s is not None and record.first_visible_s <= record.first_content_s


async def test_v4_discards_the_speculative_reply_when_the_gate_runs():
    agents = ScriptedAgents(router=[route("request_recommendation", "budget", **ACME_SLOTS)],
                            assistant=[text("speculative chat that must not be shown")],
                            narrator=[text("Recommended P003 + P006.")])

    chunks, record = await _turn("v4", Conversation(), ACME, agents)

    assert record.speculative == "discarded" and record.ran_workflow
    assert "must not be shown" not in json.dumps(chunks)
    assert record.text == "Recommended P003 + P006."


async def test_v4_skips_the_router_for_small_talk_and_mid_run():
    agents = ScriptedAgents(router=[], assistant=[text("Hi! How can I help?"), text("Still running.")])
    _, small_talk = await _turn("v4", Conversation(), "hi there!", agents)
    running = Conversation()
    running.state["workflow_running"] = True
    _, mid_run = await _turn("v4", running, "Is it done yet?", agents)

    assert small_talk.router_skipped == "small talk" and not small_talk.tool_calls
    assert mid_run.router_skipped == "workflow running" and mid_run.gate["action"] == "status"


def test_small_talk_filter_is_conservative():
    from wt.variants import is_small_talk

    assert is_small_talk("thanks!", {})
    assert not is_small_talk("thanks, now run it for EMEA", {})
    assert not is_small_talk("hi, Retail client in the US with $30k", {})
    assert not is_small_talk("ok", {"pending_request": True})


async def test_v4_rejects_a_speculative_reply_that_invents_a_recommendation():
    agents = ScriptedAgents(router=[route("decline", "Don't run it yet")],
                            assistant=[text("I recommend Display Plus and Attention Builder."),
                                       text("Understood, I'll wait until you're ready.")])

    _, record = await _turn("v4", Conversation(), "Finance, US, $20k, CTR. Don't run it yet.", agents)

    assert record.speculative == "rejected"
    assert record.text == "Understood, I'll wait until you're ready."
