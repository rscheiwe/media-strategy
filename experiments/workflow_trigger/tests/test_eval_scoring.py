"""Eval plumbing: setup and per-turn scoring (offline)."""

import yaml

from evals.run_eval import EVALSET, build_conversation, score_turn
from wt.variants import TurnRecord

ARGS = {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30000}


def _record(text="", calls=()):
    return TurnRecord(variant="v1", user="u", text=text, tool_calls=list(calls))


def _call(status="done", **args):
    return {"name": "run_recommendation", "args": {**ARGS, **args}, "output": {"status": status}}


def test_evalset_is_well_formed():
    cases = yaml.safe_load(EVALSET.read_text())
    assert len(cases) == 40 and len({c["id"] for c in cases}) == 40
    for case in cases:
        for turn in case["turns"]:
            assert turn["expect"]["action"] in ("call", "ask", "no_call")


def test_call_scoring():
    expect = {"action": "call", "args": ARGS}
    assert score_turn(_record(calls=[_call()]), expect)["correct"]
    assert score_turn(_record(calls=[_call(kpi="in_view_rate")]), expect)["why"].startswith("wrong args")
    assert score_turn(_record(text="Sure!"), expect)["why"] == "missed trigger"


def test_no_call_and_ask_scoring():
    assert score_turn(_record(text="Hi!"), {"action": "no_call"})["correct"]
    assert score_turn(_record(calls=[_call(status="not_run")]), {"action": "no_call"})["why"].startswith("false trigger attempt")
    ask = {"action": "ask", "missing": ["vertical", "kpi"]}
    assert score_turn(_record(text="Which industry is it, and should we optimize for CTR or in-view?"), ask)["correct"]
    assert not score_turn(_record(text="Which industry is it?"), ask)["correct"]


async def test_after_result_setup_has_a_result_and_history():
    convo = await build_conversation({"setup": {"after_result": dict(ARGS)}})
    assert convo.state["last_result"]["allocation"]["lines"]
    assert [m["role"] for m in convo.history] == ["user", "assistant"]
