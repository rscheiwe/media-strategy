"""Acceptance tests for the recommendation workflow, the routing and the chat turns.

Expected data comes only from ``evals.truth``, an independent pandas calculation
over the case-study CSVs.  These tests intentionally exercise the public
workflow package; no workflow internals are mocked.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
from datetime import UTC, datetime, timedelta
from math import isclose
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from evals import truth
from chat_api.app import create_app
from chat_api.casedata import build_case_db, connect_readonly
from chat_api.chat.trigger import GateDecision, RouterDecision, WorkflowRouter, decide
from chat_api.chat.turns import TurnRunner, state_block
from chat_api.chat.workflow_tool import LOCK_TTL, dispatch, merge_slots, run_recommendation, ungrounded
from chat_api.workflow.domain import Slots
from chat_api.workflow.form import build_form
from chat_api.workflow.policy import InventoryPolicy, SelectionPolicy
from chat_api.workflow.service import artifact_title
from chat_api.workflow.steps import advertiser_names, check_scale, compute_benchmarks, missing_slots, rank_fill, recommend, select_products
from chat_api.storage.container import StorageContainer
from chat_api.storage.db.sqlite import SqliteDatabaseClient


def _value(item, name):
    """Read the public Pydantic model field without depending on serialization."""
    return getattr(item, name)


def _codes(rejections) -> dict[str, tuple[str, ...]]:
    return {
        product_id: tuple(_value(rejection, "code") for rejection in values)
        for product_id, values in rejections.items()
    }


def _slots(source: truth.TruthSlots) -> Slots:
    return Slots(**source.__dict__)


def _collect(stream):
    async def gather():
        return [event async for event in stream]

    return asyncio.run(gather())


def _frames(response):
    return [json.loads(line.removeprefix("data: ")) for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]


def _workflow_storage(path) -> StorageContainer:
    return StorageContainer.from_db(SqliteDatabaseClient(str(path)))


def _chat_id(storage: StorageContainer, title: str = "Workflow") -> str:
    return storage.chats.create_chat(
        user_id="00000000-0000-0000-0000-000000000101",
        title=title,
        agent_uuid="3b9c6f0e-6a3e-4f43-9a51-2f7e6d1c8a10",
    ).id


def _form_metadata(form_id: str, *, kpi: str = "ctr") -> dict:
    return {
        "type": "tool_form_submission",
        "target_tool": "run_recommendation",
        "form_id": form_id,
        "parameters": {"client_name": "Acme Shoes", "vertical": "Retail", "kpi": kpi, "geo": "US", "budget": 30_000, "impression_goal": None},
    }


class _NeverAgent:
    def __init__(self):
        self.instruction = "base instruction"
        self.tool_context = {}

    async def run_stream(self, *_args, **_kwargs):
        raise AssertionError("chat agent must not be built/run for direct workflow dispatch")
        yield  # pragma: no cover


@pytest.fixture()
def conn(tmp_path):
    database = tmp_path / "case.db"
    build_case_db(truth.ROOT, database)
    connection = connect_readonly(database)
    try:
        yield connection
    finally:
        connection.close()


def test_benchmarks_match_truth_for_every_vertical(conn):
    """Each vertical uses line-item ratio-of-sums, not mean-of-rates."""
    for vertical in sorted(truth.VALID_VERTICALS):
        actual = {item.product_id: item for item in compute_benchmarks(conn, vertical)}
        expected = truth.benchmarks(vertical).set_index("product_id")
        assert set(actual) == set(expected.index)
        for product_id, row in expected.iterrows():
            item = actual[product_id]
            assert _value(item, "product_name") == row.product_name
            assert _value(item, "cpm") == row.cpm
            assert _value(item, "line_items") == row.line_items
            assert _value(item, "impressions") == row.impressions
            assert _value(item, "clicks") == row.clicks
            assert _value(item, "viewable_impressions") == row.viewable_impressions
            assert isclose(_value(item, "ctr"), row.ctr, rel_tol=0, abs_tol=1e-12)
            assert isclose(_value(item, "in_view_rate"), row.in_view_rate, rel_tol=0, abs_tol=1e-12)


def test_select_products_returns_every_product_with_affordable_imps(conn):
    """Selection is ranking, not premature filtering by scale or goal."""
    slots = _slots(truth.SAMPLE_SLOTS["Burger"])
    benchmarks = compute_benchmarks(conn, slots.vertical)
    actual = {item.product_id: item for item in select_products(conn, slots, benchmarks)}
    expected = truth.candidates(truth.SAMPLE_SLOTS["Burger"]).set_index("product_id")
    assert set(actual) == set(expected.index) == {"P001", "P002", "P003", "P004", "P005", "P006"}
    for product_id, row in expected.iterrows():
        item = actual[product_id]
        assert _value(item, "product_name") == row.product_name
        assert _value(item, "cpm") == row.cpm
        assert _value(item, "kpi_rank") == row.kpi_rank
        assert _value(item, "affordable_imps") == row.affordable_imps
        assert isclose(_value(item, "kpi_value"), row.kpi_value, rel_tol=0, abs_tol=1e-12)


def test_check_scale_follows_inventory_policy(conn):
    """Capacity modes, threshold boundary and headroom are exact."""
    slots = _slots(truth.SAMPLE_SLOTS["Burger"])
    benchmarks = compute_benchmarks(conn, slots.vertical)
    candidates = select_products(conn, slots, benchmarks)
    policies = (
        truth.TruthInventoryPolicy(),
        truth.TruthInventoryPolicy(mode="raw"),
        truth.TruthInventoryPolicy(mode="threshold", min_confidence=0.80),
        truth.TruthInventoryPolicy(mode="adjusted", headroom=0.10),
    )
    for expected_policy in policies:
        actual_policy = InventoryPolicy(**expected_policy.__dict__)
        actual = {item.product_id: item for item in check_scale(conn, slots, candidates, actual_policy)}
        expected = truth.scale(truth.SAMPLE_SLOTS["Burger"], expected_policy).set_index("product_id")
        for product_id, row in expected.iterrows():
            item = actual[product_id]
            assert _value(item, "available_imps") == row.available_imps
            assert isclose(_value(item, "confidence"), row.confidence, rel_tol=0, abs_tol=1e-12)
            assert _value(item, "confidence_tier") == row.confidence_tier
            assert _value(item, "capacity") == row.capacity
            assert _value(item, "absorbs_budget") == row.absorbs_budget
            assert _value(item, "deliverable_imps") == row.deliverable_imps


def test_single_step_tools(conn):
    """Standalone tools expose the same raw-data figures as workflow steps."""
    from chat_api.chat.step_tools import CHECK_SCALE, GET_BENCHMARKS, SELECT_PRODUCTS
    from chat_api.chat.tools import build_tools

    assert {"get_benchmarks", "select_products", "check_scale"} <= {tool.name for tool in build_tools()}

    benchmarks = GET_BENCHMARKS._handler({"vertical": "Retail"}, {})
    expected_benchmarks = truth.benchmarks("Retail").set_index("product_id")
    assert benchmarks["method"].startswith("ratio of sums")
    assert len(benchmarks["benchmarks"]) == len(expected_benchmarks)
    for row in benchmarks["benchmarks"]:
        expected = expected_benchmarks.loc[row["product_id"]]
        assert row["cpm"] == expected.cpm
        assert row["line_items"] == expected.line_items
        assert row["impressions"] == expected.impressions
        assert isclose(row["ctr"], expected.ctr, rel_tol=0, abs_tol=1e-12)
        assert isclose(row["in_view_rate"], expected.in_view_rate, rel_tol=0, abs_tol=1e-12)

    slots = truth.TruthSlots(vertical="Travel", kpi="in_view_rate", geo="APAC", budget=18_000)
    ranked = SELECT_PRODUCTS._handler({"vertical": slots.vertical, "kpi": slots.kpi, "budget": slots.budget}, {})
    expected_ranked = truth.candidates(slots).set_index("product_id")
    assert {row["product_id"] for row in ranked["products"]} == set(expected_ranked.index)
    for row in ranked["products"]:
        expected = expected_ranked.loc[row["product_id"]]
        assert row["kpi_rank"] == expected.kpi_rank
        assert row["affordable_imps"] == expected.affordable_imps
        assert isclose(row["kpi_value"], expected.kpi_value, rel_tol=0, abs_tol=1e-12)

    scaled = CHECK_SCALE._handler({"vertical": slots.vertical, "geo": slots.geo, "budget": slots.budget}, {})
    expected_scale = truth.scale(slots).set_index("product_id")
    assert "Capacity = forecast × confidence" in scaled["inventory_policy"]
    for row in scaled["products"]:
        expected = expected_scale.loc[row["product_id"]]
        assert row["impressions_budget_buys"] == expected.affordable_imps
        assert row["available_imps"] == expected.available_imps
        assert row["capacity"] == expected.capacity
        assert row["absorbs_budget"] == expected.absorbs_budget
        assert row["deliverable_imps"] == expected.deliverable_imps
        assert isclose(row["confidence"], expected.confidence, rel_tol=0, abs_tol=1e-12)

    for tool, invalid in (
        (GET_BENCHMARKS, {"vertical": "LATAM"}),
        (SELECT_PRODUCTS, {"vertical": "Retail", "kpi": "cpc", "budget": 0}),
        (CHECK_SCALE, {"vertical": "Retail", "geo": "LATAM", "budget": -1}),
    ):
        error = tool._handler(invalid, {})["error"]
        assert "Invalid" in error and "Verticals:" in error and "KPIs:" in error and "geos:" in error


def test_sample_briefs_match_truth(conn):
    """Five complete briefs match raw-data truth, including rejections."""
    for name, expected_slots in truth.SAMPLE_SLOTS.items():
        expected_plan, expected_rows = truth.recommend(expected_slots)
        actual = recommend(conn, _slots(expected_slots))
        assert actual.plan.kind == expected_plan.kind, name
        assert [(line.product_id, line.spend, line.impressions) for line in actual.plan.lines] == [
            (line.product_id, line.spend, line.impressions) for line in expected_plan.lines
        ], name
        assert actual.plan.total_spend == expected_plan.total_spend, name
        assert actual.plan.unspent == expected_plan.unspent, name
        assert actual.plan.total_impressions == expected_plan.total_impressions, name
        assert actual.plan.meets_goal == expected_plan.meets_goal, name
        expected_rejections = {
            row.product_id: truth.rejection_codes(row, expected_slots, truth.TruthInventoryPolicy())
            for _, row in expected_rows.iterrows()
            if truth.rejection_codes(row, expected_slots, truth.TruthInventoryPolicy())
        }
        actual_rejections = _codes(actual.rejections)
        assert {product_id: codes for product_id, codes in actual_rejections.items() if codes} == expected_rejections, name


def test_alternatives_explain_every_higher_ranked_product(conn):
    """A rejected KPI leader cannot disappear from the recommendation."""
    for name, expected_slots in truth.SAMPLE_SLOTS.items():
        actual = recommend(conn, _slots(expected_slots))
        picked_ranks = {candidate.product_id: candidate.kpi_rank for candidate in actual.candidates if candidate.product_id in {line.product_id for line in actual.plan.lines}}
        best_pick_rank = min(picked_ranks.values())
        above_pick = {candidate.product_id for candidate in actual.candidates if candidate.kpi_rank < best_pick_rank}
        alternatives = {tuple(alternative.product_ids): alternative for alternative in actual.alternatives}
        alternative_ids = {product_id for ids in alternatives for product_id in ids}
        assert above_pick <= alternative_ids, name
        for product_id in above_pick:
            alternative = next(alternative for ids, alternative in alternatives.items() if product_id in ids)
            assert alternative.reasons, (name, product_id)
            assert all(reason in {"INVENTORY_BELOW_BUDGET", "BUDGET_BELOW_GOAL", "INVENTORY_BELOW_GOAL", "BELOW_MIN_CONFIDENCE"} for reason in alternative.reasons), (name, product_id)
            assert alternative.delta.get("kpi_points") is not None, (name, product_id)
            assert alternative.delta.get("impression_shortfall") is not None or alternative.delta.get("unabsorbed_spend") is not None, (name, product_id)
        expected_bundle = truth.best_bundle(expected_slots)
        if actual.plan.blended_kpi and expected_bundle.blended_kpi and expected_bundle.blended_kpi / actual.plan.blended_kpi - 1 >= 0.05:
            assert tuple(line.product_id for line in expected_bundle.lines) in alternatives, name


@pytest.mark.parametrize(
    "slots,inventory_policy,selection_policy",
    [
        (truth.TruthSlots(vertical="Retail", kpi="ctr", geo="US", budget=20_000, impression_goal=99_999_999), truth.TruthInventoryPolicy(), truth.TruthSelectionPolicy()),
        (truth.TruthSlots(vertical="Retail", kpi="ctr", geo="US", budget=500), truth.TruthInventoryPolicy(), truth.TruthSelectionPolicy()),
        (truth.TruthSlots(vertical="QSR", kpi="in_view_rate", geo="US", budget=15_000), truth.TruthInventoryPolicy(), truth.TruthSelectionPolicy()),
        (truth.TruthSlots(vertical="QSR", kpi="in_view_rate", geo="US", budget=15_000), truth.TruthInventoryPolicy(mode="threshold", min_confidence=0.80), truth.TruthSelectionPolicy()),
        (truth.TruthSlots(vertical="Travel", kpi="in_view_rate", geo="APAC", budget=1_000_000), truth.TruthInventoryPolicy(), truth.TruthSelectionPolicy()),
        (truth.TruthSlots(vertical="Travel", kpi="in_view_rate", geo="APAC", budget=18_000, impression_goal=850_000), truth.TruthInventoryPolicy(), truth.TruthSelectionPolicy(mode="kpi_max")),
    ],
)
def test_plan_invariants_hold_for_all_cases(conn, slots, inventory_policy, selection_policy):
    """Constraints and policy-optimality hold on normal and requested edges."""
    actual = recommend(
        conn,
        _slots(slots),
        inventory_policy=InventoryPolicy(**inventory_policy.__dict__),
        selection_policy=SelectionPolicy(**selection_policy.__dict__),
    )
    expected_plan, _ = truth.recommend(slots, inventory_policy, selection_policy)
    assert actual.plan.kind == expected_plan.kind
    assert [(line.product_id, line.spend, line.impressions) for line in actual.plan.lines] == [(line.product_id, line.spend, line.impressions) for line in expected_plan.lines]
    assert actual.plan.total_spend <= slots.budget
    assert len(actual.plan.lines) <= selection_policy.max_lines
    scale_by_id = {check.product_id: check for check in actual.scale}
    for line in actual.plan.lines:
        assert line.spend >= selection_policy.min_line_spend
        assert line.impressions <= scale_by_id[line.product_id].capacity
    if slots.impression_goal is None:
        assert actual.plan.meets_goal is None
    else:
        assert actual.plan.meets_goal == (actual.plan.total_impressions >= slots.impression_goal)


def test_missing_slots_return_prefilled_form():
    """Deterministic core: invalid/missing values are exact and pre-filled."""
    for source, expected_missing, expected_values in (
        (
            truth.TruthSlots(client_name="Mobile", kpi="in_view_rate", geo="US", budget=25_000),
            ["vertical"],
            {"client_name": "Mobile", "vertical": None, "kpi": "in_view_rate", "geo": "US", "budget": 25_000, "impression_goal": None},
        ),
        (
            truth.TruthSlots(client_name="Finance brand", vertical="Finance", geo="EMEA", budget=25_000),
            ["kpi"],
            {"client_name": "Finance brand", "vertical": "Finance", "kpi": None, "geo": "EMEA", "budget": 25_000, "impression_goal": None},
        ),
        (
            truth.TruthSlots(vertical="LATAM", kpi="clicks", geo="LATAM", budget=0),
            ["vertical", "kpi", "geo", "budget"],
            {"client_name": None, "vertical": None, "kpi": None, "geo": None, "budget": None, "impression_goal": None},
        ),
    ):
        actual_slots = _slots(source)
        missing = missing_slots(actual_slots)
        assert missing == expected_missing
        form = build_form(actual_slots, missing, form_id="acceptance-form")
        assert form["status"] == "FORM_REQUIRED"
        assert form["form_id"] == "acceptance-form"
        assert form["missing"] == expected_missing
        fields = {field["name"]: field for field in form["form_config"]["fields"]}
        assert set(fields) == {"client_name", "vertical", "kpi", "geo", "budget", "impression_goal"}
        for name, expected_value in expected_values.items():
            assert fields[name]["value"] == expected_value
            assert fields[name]["missing"] is (name in expected_missing)


def test_form_submission_runs_without_llm_and_rejects_stale_forms(tmp_path):
    """The form path dispatches directly; explain may still use its template."""
    storage = _workflow_storage(tmp_path / "workflow.db")
    chat_id = _chat_id(storage)
    state = storage.workflow_state.get(chat_id)
    state.active_form_id, state.pending_request = "current-form", True
    storage.workflow_state.save(state)
    built = 0

    def build_agent():
        nonlocal built
        built += 1
        return _NeverAgent()

    class NeverRouter:
        async def run(self, *_args, **_kwargs):
            raise AssertionError("router must not run for a form submission")
            yield  # pragma: no cover

    runner = TurnRunner(build_agent, trigger="tool", chat_id=chat_id, metadata=_form_metadata("current-form"), workflow_state=storage.workflow_state, router=NeverRouter(), user_text="submitted form", turn=2)
    events = _collect(runner.run_stream({"messages": []}))
    assert built == 0
    output = next(event["output"] for event in events if event.get("type") == "tool-output-available")
    assert output["status"] == "done"
    assert output["summary"]["kind"] == "single"
    assert storage.workflow_state.get(chat_id).last_run is not None

    stale = TurnRunner(build_agent, trigger="tool", chat_id=chat_id, metadata=_form_metadata("old-form"), workflow_state=storage.workflow_state, router=NeverRouter())
    stale_events = _collect(stale.run_stream({"messages": []}))
    assert built == 0
    assert next(event["output"] for event in stale_events if event.get("type") == "tool-output-available") == {"status": "not_run", "reason": "stale_form"}


def test_pending_request_survives_restart(tmp_path, monkeypatch):
    """File-backed state resumes both a submitted form and a B free-text answer."""
    path = tmp_path / "restart.db"
    first = _workflow_storage(path)
    form_chat = _chat_id(first)
    text_chat = _chat_id(first)
    form_state = first.workflow_state.get(form_chat)
    form_state.active_form_id, form_state.pending_request = "restart-form", True
    form_state.slots = {"vertical": {"value": "Retail", "source": "llm", "turn": 1}}
    first.workflow_state.save(form_state)

    second = _workflow_storage(path)
    form_runner = TurnRunner(lambda: _NeverAgent(), trigger="tool", chat_id=form_chat, metadata=_form_metadata("restart-form"), workflow_state=second.workflow_state)
    assert next(event["output"] for event in _collect(form_runner.run_stream({"messages": []})) if event.get("type") == "tool-output-available")["status"] == "done"
    restored = second.workflow_state.get(form_chat)
    assert restored.pending_request is False and restored.active_form_id is None and restored.last_run

    text_state = second.workflow_state.get(text_chat)
    text_state.pending_request, text_state.asked_for = True, ["kpi"]
    text_state.slots = {"vertical": {"value": "Finance", "source": "llm", "turn": 1}, "geo": {"value": "US", "source": "llm", "turn": 1}, "budget": {"value": 20_000, "source": "llm", "turn": 1}}
    second.workflow_state.save(text_state)

    async def classified(self, *_args):
        return RouterDecision(intent="provide_details", kpi="ctr", evidence_quote="CTR")

    monkeypatch.setattr(WorkflowRouter, "classify", classified)
    router_runner = TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=text_chat, metadata=None, workflow_state=second.workflow_state, user_text="CTR", turn=2, router=WorkflowRouter())
    events = _collect(router_runner.run_stream({"messages": [{"role": "user", "content": "CTR"}]}))
    assert any(event.get("type") == "tool-output-available" and event["output"]["status"] == "done" for event in events)
    assert second.workflow_state.get(text_chat).last_run is not None


def test_grounding_checks_only_new_or_changed_model_slots():
    """Regression: persisted facts survive a short next-turn answer unchallenged."""
    original = {"vertical": {"value": "Finance", "source": "llm", "turn": 1}}
    # (a) A guessed current-turn vertical lacks lexical support and must be confirmed.
    guessed = merge_slots({}, {"vertical": "Retail"}, "llm", 1)
    assert ungrounded(guessed, "a bank needs a plan", 1) == ["vertical"]
    # (b) Repeating a known fact does not make it a new inference on the CTR turn.
    repeated = merge_slots(original, {"vertical": "Finance", "kpi": "ctr"}, "llm", 2)
    assert repeated["vertical"] == original["vertical"]
    assert ungrounded(repeated, "CTR", 2) == []
    # (c) A real replacement is current-turn data and must be grounded against that turn.
    changed = merge_slots(original, {"vertical": "Retail"}, "llm", 2)
    assert changed["vertical"]["turn"] == 2
    assert ungrounded(changed, "actually retail", 2) == []


def test_form_intro_before_form(tmp_path, monkeypatch):
    """The real router/gate/DAG emits an intro before its clarification form."""
    storage = _workflow_storage(tmp_path / "intro.db")
    chat_id = _chat_id(storage)

    async def classified(_self, *_args):
        return RouterDecision(intent="request_recommendation", vertical="Retail", evidence_quote="Retail")

    monkeypatch.setattr(WorkflowRouter, "classify", classified)

    class IntroAgent:
        async def run_stream(self, *_args, **_kwargs):
            yield {"type": "text-delta", "delta": "I have your Retail brief; please fill in the remaining details below."}

    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=lambda **_kwargs: IntroAgent()))
    runner = TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=None, workflow_state=storage.workflow_state, user_text="Retail", turn=1, router=WorkflowRouter())
    events = _collect(runner.run_stream({"messages": [{"role": "user", "content": "Retail"}]}))
    intro_start = next(i for i, event in enumerate(events) if event.get("type") == "text-start" and event.get("id") == "form-intro")
    intro_end = next(i for i, event in enumerate(events) if event.get("type") == "text-end" and event.get("id") == "form-intro")
    form = next((i, event) for i, event in enumerate(events) if event.get("type") == "tool-output-available" and event["output"].get("status") == "FORM_REQUIRED")
    assert intro_start < intro_end < form[0]
    assert "remaining details" in "".join(event.get("delta", "") for event in events[intro_start:intro_end + 1])
    assert form[1]["output"]["missing"] == ["kpi", "geo", "budget"]

    class EmptyIntroAgent:
        async def run_stream(self, *_args, **_kwargs):
            if False:
                yield {}

    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=lambda **_kwargs: EmptyIntroAgent()))
    fallback = _collect(WorkflowRouter()._intro("Retail", GateDecision("ask", missing=["kpi", "geo"])))
    assert "primary KPI and geo" in "".join(event.get("delta", "") for event in fallback)
    assert artifact_title(Slots(vertical="Finance", geo="APAC")) == "Finance · APAC"


def test_client_suggestions_and_router_boundary(conn):
    """Brief advertisers remain optional suggestions and gate decisions preserve the boundary."""
    suggestions = advertiser_names(conn)
    assert suggestions == ["Acme Shoes", "Burger Bazaar", "CineVerse", "Stellar Bank", "Wanderlust Air"]
    form = build_form(Slots(client_name="Any advertiser"), ["vertical", "kpi", "geo", "budget"], form_id="suggestions", client_suggestions=suggestions)
    assert form["form_config"]["fields"][0] == {
        "name": "client_name", "label": "Client", "type": "text", "required": False,
        "value": "Any advertiser", "missing": False, "inferred": False,
        "placeholder": "Type or select an advertiser…", "suggestions": suggestions,
    }
    choose = "What would suit a Retail brand optimizing for clicks?"
    data = "Which product has the best CTR in Retail?"
    assert decide(RouterDecision(intent="request_recommendation", evidence_quote="what would suit"), choose, {}, pending=False, has_result=False, running=False).action == "ask"
    assert decide(RouterDecision(intent="general_chat", evidence_quote="best CTR"), data, {}, pending=False, has_result=False, running=False).action == "chat"


def test_analysis_uses_inventory_policy(conn, monkeypatch):
    """Analysis expressions and generated guidance share scale's policy rule."""
    import pandas as pd
    import chat_api.chat.analysis as analysis
    import chat_api.chat.prompts as prompts
    from chat_api.workflow.policy import InventoryPolicy

    slots = Slots(vertical="QSR", geo="US", budget=15_000, kpi="ctr")
    candidates = select_products(conn, slots, compute_benchmarks(conn, slots.vertical))
    frame = pd.read_sql_query(
        "SELECT product_id, available_imps, inventory_risk FROM inventory WHERE vertical = 'QSR' AND geo = 'US'",
        conn,
    ).set_index("product_id")
    for mode in ("adjusted", "raw", "threshold"):
        monkeypatch.setenv("APP_INVENTORY_POLICY", mode)
        policy = InventoryPolicy(mode=mode)
        usable = eval(policy.pandas_expr("b"), {"__builtins__": {"int": int}}, {"b": frame})
        scale = {row.product_id: row.capacity for row in check_scale(conn, slots, candidates, policy)}
        assert {product_id: int(value) for product_id, value in usable.items()} == scale
        analysis = importlib.reload(analysis)
        prompts = importlib.reload(prompts)
        assert policy.rule() in analysis.RECIPES
        assert policy.rule() in analysis.RECIPE_NOTES
        assert f"usable inventory = {policy.rule()}" in prompts.ASSISTANT_SYSTEM_PROMPT
    monkeypatch.delenv("APP_INVENTORY_POLICY", raising=False)
    importlib.reload(analysis)
    importlib.reload(prompts)


def test_stages_and_artifact_stream_persist_and_reload(tmp_path):
    """A real HTTP form turn persists final stage/artifact parts exactly once."""
    path = tmp_path / "parts.db"
    storage = _workflow_storage(path)
    chat = storage.chats.create_chat(user_id="00000000-0000-0000-0000-000000000101", title="Workflow", agent_uuid="3b9c6f0e-6a3e-4f43-9a51-2f7e6d1c8a10")
    state = storage.workflow_state.get(chat.id)
    state.active_form_id, state.pending_request = "parts-form", True
    storage.workflow_state.save(state)

    with TestClient(create_app(storage=storage, agent_factory=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("agent built")))) as client:
        response = client.post("/app/chat", headers={"X-User-Id": "00000000-0000-0000-0000-000000000101"}, json={"chat_id": chat.id, "agent_uuid": "assistant", "messages": [{"role": "user", "content": "Submit", "parts": [{"type": "text", "text": "Submit"}]}], "metadata": _form_metadata("parts-form")})
        assert response.status_code == 200
        frames = _frames(response)
        stages = [frame for frame in frames if frame.get("type") == "data-workflow-stage"]
        artifacts = [frame for frame in frames if frame.get("type") == "data-artifact-recommendation"]
        assert stages and artifacts
        assert all(stage["id"].startswith("wf-") and stage["data"]["status"] in {"running", "done", "error", "skipped"} for stage in stages)
        assert artifacts[-1]["data"]["status"] == "complete"
        assert artifacts[-1]["data"]["payload"]["plan"]
        reloaded = client.get(f"/app/chats/{chat.id}", headers={"X-User-Id": "00000000-0000-0000-0000-000000000101"}).json()["messages"]
        parts = reloaded[-1]["parts"]
        ids = [part.get("id") for part in parts if part.get("type", "").startswith("data-")]
        assert len(ids) == len(set(ids))
        assert any(part.get("type") == "data-artifact-recommendation" and part["data"]["status"] == "complete" for part in parts)


def test_explainer_input_is_complete_and_fallback_only(tmp_path):
    """Every decision fact reaches the explainer; only failures use a template."""
    from chat_api.workflow.explain import explain_input
    from chat_api.workflow.service import run_workflow

    conn = connect_readonly(build_case_db(truth.ROOT, tmp_path / "explain-case.db"))
    recommendations = {
        name: recommend(conn, _slots(truth.SAMPLE_SLOTS[name]))
        for name in ("Acme", "Burger", "CineVerse")
    }
    recommendations["QSR line limit"] = recommend(
        conn, Slots(vertical="QSR", kpi="in_view_rate", geo="EMEA", budget=40_000)
    )
    conn.close()

    # These briefs jointly exercise rejected leaders, a bundle and every kind of
    # tradeoff that carries decision figures.  Inspect each product row rather
    # than accepting a prose summary of the data.
    for rec in recommendations.values():
        rendered = explain_input(rec)
        assert "## Brief" in rendered
        assert f"- Inventory: {rec.policy['note']}" in rendered
        assert "- Selection: " in rendered
        assert "## Products, ranked by " in rendered
        assert "## Recommended plan" in rendered
        for candidate in rec.candidates:
            benchmark = next(item for item in rec.benchmarks if item.product_id == candidate.product_id)
            scale = next(item for item in rec.scale if item.product_id == candidate.product_id)
            row = next(line for line in rendered.splitlines() if line.startswith(f"| {candidate.kpi_rank} | {candidate.product_name}"))
            for fact in (
                f"{benchmark.ctr:.2%}", f"{benchmark.in_view_rate:.2%}", f"${candidate.cpm:,.0f}",
                f"{candidate.affordable_imps:,}", f"{scale.available_imps:,}", f"{scale.confidence:.2f}",
                f"{scale.capacity:,}",
            ):
                assert fact in row
            if rec.rejections[candidate.product_id]:
                assert "no: " in row
                assert not any(rejection.code in row for rejection in rec.rejections[candidate.product_id])
                # A generic "not viable" is insufficient: the reason needs the
                # computed capacity/need/shortfall (or goal/confidence) figures.
                for rejection in rec.rejections[candidate.product_id]:
                    for value in rejection.detail.values():
                        expected = f"{value:,.0f}" if isinstance(value, int) else f"{value:.2f}"
                        assert expected in row
            else:
                assert row.endswith("| yes |")
        for line in rec.plan.lines:
            assert line.product_name in rendered
            assert f"${line.spend:,.2f}" in rendered or f"${line.spend:,.0f}" in rendered
            assert f"{line.impressions:,}" in rendered
        for alternative in rec.alternatives:
            for product_id in alternative.product_ids:
                assert next(candidate.product_name for candidate in rec.candidates if candidate.product_id == product_id) in rendered
        for tradeoff in rec.tradeoffs:
            assert tradeoff.code not in rendered

    acme = explain_input(recommendations["Acme"])
    assert "would raise blended CTR by 27.6% over the pick (+0.25 points)" in acme
    assert "usable inventory covers the buy with only 0.32% to spare" in acme
    burger = explain_input(recommendations["Burger"])
    assert "within 0.12 points of the pick" in burger
    assert "exceeds the 800,000 impression goal by 33.93%" in burger
    cineverse = explain_input(recommendations["CineVerse"])
    assert "Total: $38,164.56 spend, 1,818,300 impressions" in cineverse
    assert "$1,835.44 of the budget stays unspent: it's below the minimum line spend" in cineverse
    qsr_limit = recommendations["QSR line limit"]
    qsr_unspent = next(tradeoff for tradeoff in qsr_limit.tradeoffs if tradeoff.code == "UNSPENT_BUDGET")
    assert qsr_unspent.facts["reason"] == "line_limit"
    assert "$7,865.21 of the budget stays unspent: the bundle is at its 3-product limit, each product at its usable inventory" in explain_input(qsr_limit)

    async def model_text(_rec):
        return "The strategist's independently written rationale."

    delivered = next(event["recommendation"] for event in _collect(run_workflow(
        _slots(truth.SAMPLE_SLOTS["Acme"]), run_id="llm-rationale", explainer=model_text
    )) if event.get("type") == "workflow-result")
    assert delivered.rationale.source == "llm"
    assert delivered.rationale.text == "The strategist's independently written rationale."

    async def broken(_rec):
        raise RuntimeError("provider unavailable")

    async def empty(_rec):
        return ""

    for run_id, explainer in (("failed-rationale", broken), ("empty-rationale", empty)):
        fallback = next(event["recommendation"] for event in _collect(run_workflow(
            _slots(truth.SAMPLE_SLOTS["Acme"]), run_id=run_id, explainer=explainer
        )) if event.get("type") == "workflow-result")
        assert fallback.rationale.source == "template"
        assert fallback.plan.lines[0].product_name in fallback.rationale.text


def test_empty_reply_retry(tmp_path):
    """An empty model result retries once without changing turn framing."""
    from chat_api.chat.turns import EMPTY_REPLY_FALLBACK, stream_with_empty_retry

    class Scripts:
        def __init__(self, runs):
            self.runs, self.calls = runs, 0

        async def stream(self):
            run = self.runs[self.calls]
            self.calls += 1
            for event in run:
                yield event

    empty = [{"type": "start"}, {"type": "error", "errorText": "No response from LLM"}, {"type": "finish"}]
    normal = [{"type": "start"}, {"type": "text-start", "id": "reply"}, {"type": "text-delta", "id": "reply", "delta": "Thanks!"}, {"type": "text-end", "id": "reply"}, {"type": "finish"}]

    async def exercise(runs):
        scripts = Scripts(runs)
        events = [event async for event in stream_with_empty_retry(scripts.stream)]
        return scripts.calls, events

    calls, events = asyncio.run(exercise([normal]))
    assert calls == 1 and [event["type"] for event in events].count("start") == 1

    calls, events = asyncio.run(exercise([empty, normal]))
    assert calls == 2
    assert any(event.get("delta") == "Thanks!" for event in events)
    assert [event["type"] for event in events].count("start") == [event["type"] for event in events].count("finish") == 1

    calls, events = asyncio.run(exercise([empty, empty]))
    assert calls == 2
    assert [event.get("delta") for event in events if event.get("type") == "text-delta"] == [EMPTY_REPLY_FALLBACK]
    assert [event["type"] for event in events].count("start") == [event["type"] for event in events].count("finish") == 1

    tool_then_empty = [{"type": "start"}, {"type": "tool-input-start", "toolCallId": "call-1", "toolName": "list_products"}, *empty[1:]]
    calls, events = asyncio.run(exercise([tool_then_empty]))
    assert calls == 1
    assert any(event.get("type") == "tool-input-start" for event in events)
    assert any(event.get("delta") == EMPTY_REPLY_FALLBACK for event in events)

    rate_limited = [{"type": "start"}, {"type": "error", "errorText": "429 too many requests"}, {"type": "finish"}]
    calls, events = asyncio.run(exercise([rate_limited]))
    assert calls == 1
    assert any(event.get("errorText") == "429 too many requests" for event in events)
    assert not any(event.get("delta") == EMPTY_REPLY_FALLBACK for event in events)

    class FakeAgent:
        instruction = ""
        tool_context = {}

        def __init__(self):
            self.runs, self.calls = [empty, normal], 0

        async def run_stream(self, *_args, **_kwargs):
            run = self.runs[self.calls]
            self.calls += 1
            for event in run:
                yield event

    agents = []

    def factory(**_kwargs):
        agent = FakeAgent()
        agents.append(agent)
        return agent

    storage = StorageContainer.from_db(SqliteDatabaseClient(str(tmp_path / "empty-retry.db")))
    user = "00000000-0000-0000-0000-000000000101"
    with TestClient(create_app(storage=storage, agent_factory=factory)) as client:
        for agent_uuid in ("assistant", "planner-tool"):
            response = client.post("/app/chat", headers={"X-User-Id": user}, json={
                "agent_uuid": agent_uuid,
                "messages": [{"role": "user", "content": "Great, thanks!", "parts": [{"type": "text", "text": "Great, thanks!"}]}],
            })
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: {")]
            assert [frame["type"] for frame in frames].count("start") == 1
            assert [frame["type"] for frame in frames].count("finish") == 1
            assert any(frame.get("delta") == "Thanks!" for frame in frames)
    assert len(agents) == 2 and all(agent.calls == 2 for agent in agents)


def test_prompt_has_local_datetime(tmp_path):
    """The request zone becomes a local, offset-bearing system-prompt stamp."""
    from chat_api.chat.agents import with_current_date

    instant = datetime(2026, 10, 5, 22, 21, tzinfo=UTC)
    local = with_current_date("BASE", now=instant, tz="America/New_York")
    assert "Monday, 2026-10-05 18:21 (America/New_York, UTC-04:00), the user's local time." in local
    for zone in (None, "Mars/Olympus"):
        fallback = with_current_date("BASE", now=instant, tz=zone)
        assert "Monday, 2026-10-05 22:21 (UTC, UTC+00:00); the user's time zone is unknown." in fallback

    seen = []

    class FakeAgent:
        instruction = ""
        tool_context = {}

        async def run_stream(self, *_args, **_kwargs):
            yield {"type": "start"}
            yield {"type": "text-start", "id": "time"}
            yield {"type": "text-delta", "id": "time", "delta": "It is 18:21."}
            yield {"type": "text-end", "id": "time"}
            yield {"type": "finish"}

    def factory(**kwargs):
        seen.append(kwargs["timezone"])
        return FakeAgent()

    storage = StorageContainer.from_db(SqliteDatabaseClient(str(tmp_path / "timezone.db")))
    with TestClient(create_app(storage=storage, agent_factory=factory)) as client:
        response = client.post("/app/chat", headers={"X-User-Id": "00000000-0000-0000-0000-000000000101"}, json={
            "timezone": "America/New_York",
            "messages": [{"role": "user", "content": "Hello", "parts": [{"type": "text", "text": "Hello"}]}],
        })
    assert response.status_code == 200
    assert seen == ["America/New_York"]


def test_approach_a_tool_call_ends_turn(monkeypatch, tmp_path):
    """A's ToolSpec is the shared handler and stops after its output."""
    from chat_api.chat.agents import agent_tools, get_chat_agent_config
    from chat_api.chat.workflow_tool import RUN_RECOMMENDATION

    config = get_chat_agent_config("planner-tool")
    # The request router resolves a selected agent through its UUID a second
    # time.  A shared UUID would silently turn planner-tool into the default B
    # config, so protect the full lookup path, not merely the named alias.
    for name in ("assistant", "planner-tool", "planner-router"):
        selected = get_chat_agent_config(name)
        resolved = get_chat_agent_config(selected.uuid)
        assert (resolved.id, resolved.workflow_trigger) == (selected.id, selected.workflow_trigger)
    assert any(tool.name == "run_recommendation" for tool in agent_tools(get_chat_agent_config("planner-tool").workflow_trigger))
    assert config.workflow_trigger == "tool"
    assert RUN_RECOMMENDATION.name == "run_recommendation"
    assert config.policies["tool_behavior"]["run_recommendation"]["stop_on_first_use"] is True
    storage = _workflow_storage(tmp_path / "a.db")
    chat_id = _chat_id(storage)
    events = _collect(dispatch({"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}, {"chat_id": chat_id, "workflow_state": storage.workflow_state, "user_text": "Retail US $30k CTR", "turn": 1, "slot_source": "llm"}))
    assert sum(event.get("type") == "tool-output-available" for event in events) == 1
    assert not any(event.get("type") == "text-delta" for event in events)


def test_approach_b_dispatches_without_llm_tool_choice(tmp_path, monkeypatch):
    """B emits synthetic tool events for run/ask and gives chat no workflow tool."""
    from chat_api.chat.agents import agent_tools, get_chat_agent_config

    # The production assistant defaults to the evaluated B path; planner-tool
    # remains the explicit A comparison agent.
    assert get_chat_agent_config("assistant").workflow_trigger == "router"
    assert get_chat_agent_config("planner-router").workflow_trigger == "router"
    assert all(tool.name != "run_recommendation" for tool in agent_tools("router"))

    async def classified(self, *_args):
        return RouterDecision(intent="request_recommendation", vertical="Retail", kpi="ctr", geo="US", budget=30_000, evidence_quote="Retail")

    monkeypatch.setattr(WorkflowRouter, "classify", classified)
    storage = _workflow_storage(tmp_path / "b.db")
    chat_id = _chat_id(storage)
    runner = TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=None, workflow_state=storage.workflow_state, user_text="Retail US $30k CTR", turn=1, router=WorkflowRouter())
    events = _collect(runner.run_stream({"messages": [{"role": "user", "content": "Retail US $30k CTR"}]}))
    assert any(event.get("type") == "tool-input-start" and event.get("toolName") == "run_recommendation" for event in events)
    assert any(event.get("type") == "tool-output-available" and event["output"]["status"] == "done" for event in events)


def test_idempotent_replay_and_run_lock(tmp_path):
    """Result cache, fresh lock, and >120s stale lock are distinguishable."""
    storage = _workflow_storage(tmp_path / "locks.db")
    chat_id = _chat_id(storage)
    ctx = {"chat_id": chat_id, "workflow_state": storage.workflow_state, "user_text": "Retail US $30k CTR", "turn": 1, "slot_source": "llm"}
    args = {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}
    first = _collect(run_recommendation(args, ctx))
    first_output = next(event["output"] for event in first if event.get("type") == "tool-output")
    second = _collect(run_recommendation(args, ctx))
    second_output = next(event["output"] for event in second if event.get("type") == "tool-output")
    assert first_output["status"] == second_output["status"] == "done"
    assert second_output["replayed"] is True and second_output["run_id"] == first_output["run_id"]
    state = storage.workflow_state.get(chat_id)
    state.last_run = None
    state.running_since = datetime.now(UTC)
    storage.workflow_state.save(state)
    locked = _collect(run_recommendation(args, ctx))
    assert next(event["output"] for event in locked if event.get("type") == "tool-output") == {"status": "not_run", "reason": "already_running"}
    state.running_since = datetime.now(UTC) - LOCK_TTL - timedelta(seconds=1)
    storage.workflow_state.save(state)
    assert next(event["output"] for event in _collect(run_recommendation(args, ctx)) if event.get("type") == "tool-output")["status"] == "done"


def test_state_block_in_next_turn_prompt(tmp_path):
    """The next agent gets persisted facts and recommendation summary as system context."""
    storage = _workflow_storage(tmp_path / "state.db")
    chat_id = _chat_id(storage)
    ctx = {"chat_id": chat_id, "workflow_state": storage.workflow_state, "user_text": "Retail US $30k CTR", "turn": 1, "slot_source": "llm"}
    _collect(run_recommendation({"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}, ctx))
    agent = _NeverAgent()
    runner = TurnRunner(lambda: agent, trigger="tool", chat_id=chat_id, metadata=None, workflow_state=storage.workflow_state)
    runner.prepare_agent(agent)
    assert "<recommendation_state>" in agent.instruction
    assert "Retail" in agent.instruction and "Acme" not in agent.instruction
    assert "latest recommendation" in agent.instruction


def test_confirm_before_run(tmp_path, monkeypatch):
    """Carried-over and value-free changes require confirmation, never replay."""
    known = {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}
    request = RouterDecision(intent="request_recommendation", evidence_quote="run a workflow")
    assert decide(request, "let's run a workflow", known, pending=False, has_result=False, running=False).action == "confirm"
    changed = RouterDecision(intent="request_recommendation", evidence_quote="different KPI")
    gate = decide(changed, "a different KPI", known, pending=False, has_result=True, running=False)
    assert (gate.action, gate.highlight) == ("confirm", ["kpi"])
    assert decide(RouterDecision(intent="request_recommendation", budget=40_000, evidence_quote="$40k"), "what if the budget were $40k?", known, pending=False, has_result=True, running=False).action == "run"
    assert decide(RouterDecision(intent="provide_details", evidence_quote="CTR", kpi="ctr"), "CTR", {**known, "kpi": None}, pending=True, has_result=False, running=False).action == "run"
    assert decide(RouterDecision(intent="provide_details", evidence_quote="first one"), "the first one", known, pending=True, has_result=False, running=False).action == "run"

    storage = _workflow_storage(tmp_path / "confirm.db")
    chat_id = _chat_id(storage)
    # A matching completed run makes a silent idempotent replay observable.
    _collect(run_recommendation(known, {"chat_id": chat_id, "workflow_state": storage.workflow_state, "user_text": "Retail US $30k CTR", "turn": 1, "slot_source": "llm"}))

    async def classified(_self, *_args):
        return RouterDecision(intent="request_recommendation", evidence_quote="different KPI")

    class IntroAgent:
        async def run_stream(self, *_args, **_kwargs):
            yield {"type": "text-delta", "delta": "Please confirm or change the KPI below."}

    monkeypatch.setattr(WorkflowRouter, "classify", classified)
    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=lambda **_kwargs: IntroAgent()))
    runner = TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=None, workflow_state=storage.workflow_state, user_text="a different KPI", turn=2, router=WorkflowRouter())
    events = _collect(runner.run_stream({"messages": [{"role": "user", "content": "a different KPI"}]}))
    output = next(event["output"] for event in events if event.get("type") == "tool-output-available")
    assert output["status"] == "FORM_REQUIRED" and output["missing"] == []
    assert next(field for field in output["form_config"]["fields"] if field["name"] == "kpi")["inferred"] is True
    submitted = TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=_form_metadata(output["form_id"]), workflow_state=storage.workflow_state)
    submitted_events = _collect(submitted.run_stream({"messages": []}))
    assert next(event["output"] for event in submitted_events if event.get("type") == "tool-output-available")["status"] == "done"


def test_turns_say_what_they_do(tmp_path, monkeypatch):
    """Intros announce every workflow mode and form submissions use fixed prose."""
    from chat_api.chat.agents import ASSISTANT_SYSTEM_PROMPT

    captured = []

    class IntroAgent:
        async def run_stream(self, payload, **_kwargs):
            captured.append(json.loads(payload["messages"][0]["content"]))
            yield {"type": "text-delta", "delta": "I’ll explain what happens next."}

    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=lambda **_kwargs: IntroAgent()))
    router = WorkflowRouter()
    for gate in (GateDecision("run", args={"kpi": "ctr", "budget": 15_000}), GateDecision("ask", missing=["geo"]), GateDecision("confirm", notes=["change without a value"], highlight=["kpi"])):
        events = _collect(router._intro("brief", gate, {"kpi": "ctr", "budget": 15_000}))
        assert [event["type"] for event in events] == ["text-start", "text-delta", "text-end"]
    assert captured[0]["known_details"] == {"kpi": "CTR", "budget": "$15,000"}
    assert "running now" in captured[0]["situation"] and "still needed" in captured[1]["situation"] and "change" in captured[2]["situation"]
    assert "Before you call a tool, say in one short sentence what you're about to do and why" in ASSISTANT_SYSTEM_PROMPT

    storage = _workflow_storage(tmp_path / "form-turn.db")
    chat_id = _chat_id(storage)
    state = storage.workflow_state.get(chat_id)
    state.active_form_id, state.pending_request = "form-submit", True
    storage.workflow_state.save(state)
    runner = TurnRunner(lambda: (_ for _ in ()).throw(AssertionError("chat agent built")), trigger="router", chat_id=chat_id, metadata=_form_metadata("form-submit"), workflow_state=storage.workflow_state)
    events = _collect(runner.run_stream({"messages": []}))
    lead = next(i for i, event in enumerate(events) if event.get("id") == "form-submit" and event.get("type") == "text-delta")
    tool = next(i for i, event in enumerate(events) if event.get("type") == "tool-input-start")
    assert events[lead]["delta"] == "Thanks, running the recommendation on those details now."
    assert lead < tool


def test_follow_up_delivery_options_and_routing(tmp_path, monkeypatch):
    """Only delivered cards close with result-derived next steps."""
    from chat_api.chat.follow_up import delivered, follow_up_events, follow_up_fallback, follow_up_options
    from chat_api.chat.trigger import ROUTER_PROMPT

    single = {"requirements": {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}, "plan": {"kind": "single", "unspent": 0}}
    bundle = {"requirements": {"vertical": "QSR", "kpi": "in_view_rate", "geo": "EMEA", "budget": 40_000}, "plan": {"kind": "bundle", "unspent": 7_865.21}}
    none = {"requirements": {"vertical": "Travel", "kpi": "ctr", "geo": "APAC", "budget": 15_000}, "plan": {"kind": "none"}}
    assert follow_up_options(single)["options"] == [
        "re-run it with a different budget than $30,000",
        "compare Retail inventory for the top CTR products in EMEA and APAC",
    ]
    assert "part of $40,000 went unspent" in follow_up_options(bundle)["options"][0]
    assert "larger budget than $15,000 or a lower impression goal" in follow_up_options(none)["options"][0]
    assert follow_up_fallback(follow_up_options(single)) == (
        "Would you like to re-run it with a different budget than $30,000, or compare Retail inventory for the top CTR products in EMEA and APAC?"
    )

    agent_calls = []
    class EmptyAgent:
        async def run_stream(self, *_args, **_kwargs):
            if False:
                yield {}

    def agent_factory(**kwargs):
        agent_calls.append(kwargs)
        return EmptyAgent()
    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=agent_factory))
    events = _collect(follow_up_events(single))
    assert [event["type"] for event in events] == ["text-start", "text-delta", "text-end"]
    assert events[1]["delta"] == follow_up_fallback(follow_up_options(single))
    assert agent_calls[-1]["tools"] == [] and agent_calls[-1]["generation_config"] == {"temperature": 0}
    assert _collect(follow_up_events(None)) == []
    assert delivered({"type": "data-artifact-recommendation", "data": {"status": "streaming", "payload": single}}, None) is None
    assert delivered({"type": "data-artifact-recommendation", "data": {"status": "complete", "payload": single}}, None) == single

    known = {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30_000}
    assert decide(RouterDecision(intent="request_recommendation", budget=40_000, evidence_quote="$40k"), "sure, $40k", known, pending=False, has_result=True, running=False).action == "run"
    budget_gate = decide(RouterDecision(intent="request_recommendation", evidence_quote="different budget"), "try a different budget", known, pending=False, has_result=True, running=False)
    assert budget_gate.action == "confirm" and "budget" in budget_gate.highlight
    assert decide(RouterDecision(intent="general_chat", evidence_quote="compare the regions"), "compare the regions", known, pending=False, has_result=True, running=False).action == "chat"
    assert "short reply to the assistant's last question" in ROUTER_PROMPT

    async def classified(_self, *_args):
        return RouterDecision(intent="request_recommendation", vertical="Retail", kpi="ctr", geo="US", budget=30_000, evidence_quote="Retail")
    monkeypatch.setattr(WorkflowRouter, "classify", classified)
    storage = _workflow_storage(tmp_path / "follow-up.db")
    chat_id = _chat_id(storage)
    router_events = _collect(TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=None, workflow_state=storage.workflow_state, user_text="Retail US $30k CTR", turn=1, router=WorkflowRouter()).run_stream({"messages": [{"role": "user", "content": "Retail US $30k CTR"}]}))
    complete = next(i for i, event in enumerate(router_events) if event.get("type") == "data-artifact-recommendation" and event.get("data", {}).get("status") == "complete")
    follow = next(i for i, event in enumerate(router_events) if event.get("id") == "follow-up" and event.get("type") == "text-delta")
    assert complete < follow and router_events[-1]["type"] == "finish"
    state = storage.workflow_state.get(chat_id)
    state.active_form_id, state.pending_request = "follow-form", True
    storage.workflow_state.save(state)
    form_events = _collect(TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=_form_metadata("follow-form"), workflow_state=storage.workflow_state).run_stream({"messages": []}))
    form_complete = next(i for i, event in enumerate(form_events) if event.get("type") == "data-artifact-recommendation" and event.get("data", {}).get("status") == "complete")
    form_follow = next(i for i, event in enumerate(form_events) if event.get("id") == "follow-up" and event.get("type") == "text-delta")
    assert form_complete < form_follow
    stale_events = _collect(TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=chat_id, metadata=_form_metadata("wrong-form"), workflow_state=storage.workflow_state).run_stream({"messages": []}))
    assert not any(event.get("id") == "follow-up" for event in stale_events)

    async def missing_classified(_self, *_args):
        return RouterDecision(intent="request_recommendation", evidence_quote="recommend")
    monkeypatch.setattr(WorkflowRouter, "classify", missing_classified)
    ask_events = _collect(TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=_chat_id(storage), metadata=None, workflow_state=storage.workflow_state, user_text="recommend", turn=1, router=WorkflowRouter()).run_stream({"messages": [{"role": "user", "content": "recommend"}]}))
    assert any(event.get("output", {}).get("status") == "FORM_REQUIRED" for event in ask_events)
    assert not any(event.get("id") == "follow-up" for event in ask_events)

    async def failed_dispatch(*_args, **_kwargs):
        yield {"type": "tool-output-available", "output": {"status": "failed"}}
    import chat_api.chat.trigger as trigger_module
    monkeypatch.setattr(trigger_module, "dispatch", failed_dispatch)
    monkeypatch.setattr(WorkflowRouter, "classify", classified)
    failed_events = _collect(TurnRunner(lambda: _NeverAgent(), trigger="router", chat_id=_chat_id(storage), metadata=None, workflow_state=storage.workflow_state, user_text="Retail US $30k CTR", turn=1, router=WorkflowRouter()).run_stream({"messages": [{"role": "user", "content": "Retail US $30k CTR"}]}))
    assert any(event.get("output", {}).get("status") == "failed" for event in failed_events)
    assert not any(event.get("id") == "follow-up" for event in failed_events)

    class BrokenAgent:
        async def run_stream(self, *_args, **_kwargs):
            raise RuntimeError("provider unavailable")
            yield  # pragma: no cover
    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=lambda **_kwargs: BrokenAgent()))
    assert _collect(follow_up_events(single))[1]["delta"] == follow_up_fallback(follow_up_options(single))


def test_structured_rationale_checks_retry_and_template_fallback(conn, monkeypatch):
    """Structured output is checked, retried once, and missing leaders get templates."""
    from chat_api.workflow.explain import RationaleOutput, RejectedExplanation, _slug, check, output_schema, rejected_leaders, template_rejected, to_rationale, vel_explainer

    rec = recommend(conn, _slots(truth.SAMPLE_SLOTS["Acme"]))
    leaders = rejected_leaders(rec)
    assert leaders
    wrong = RationaleOutput(best_fit="Wrong product", rejected_top_alternatives=[], tradeoff="tradeoff")
    problems = check(wrong, rec)
    assert any("best_fit" in problem for problem in problems) and any("missing" in problem for problem in problems)
    partial = RationaleOutput(best_fit=rec.plan.lines[0].product_name, rejected_top_alternatives=[RejectedExplanation(product=leaders[0][1], explanation="Model explanation with figures.")], tradeoff="tradeoff")
    rationale = to_rationale(partial, rec)
    expected_templates = {note.product_name: note.explanation for note in template_rejected(rec)}
    for note in rationale.rejected:
        if note.product_name == leaders[0][1]:
            assert (note.source, note.explanation) == ("llm", "Model explanation with figures.")
        else:
            assert (note.source, note.explanation) == ("template", expected_templates[note.product_name])

    schema = output_schema(rec)
    nested = schema.model_fields["rejected_top_alternatives"].annotation
    expected_slugs = {name.lower().replace(" ", "_") for _, name in leaders}
    assert set(nested.model_fields) == expected_slugs
    assert set(nested.model_json_schema()["required"]) == expected_slugs
    zero = next(
        candidate
        for candidate in (recommend(conn, _slots(slots)) for slots in truth.SAMPLE_SLOTS.values())
        if not rejected_leaders(candidate)
    )
    zero_nested = output_schema(zero).model_fields["rejected_top_alternatives"].annotation
    assert zero_nested.model_fields == {}
    outputs = [
        {"best_fit": "Wrong product", "rejected_top_alternatives": {_slug(name): f"{name} could not carry the brief." for _, name in leaders}},
        {"best_fit": rec.plan.lines[0].product_name, "rejected_top_alternatives": {_slug(name): f"{name} could not carry the brief." for _, name in leaders}},
    ]
    calls = []

    class Agent:
        def __init__(self, **kwargs):
            nested_model = kwargs["output_type"].model_fields["rejected_top_alternatives"].annotation
            assert set(nested_model.model_fields) == expected_slugs
            assert kwargs["generation_config"] == {"temperature": 0}
        async def run_stream(self, *_args, **_kwargs):
            calls.append(1)
            yield {"type": "data-object-complete", "data": {"object": outputs[len(calls) - 1]}}

    monkeypatch.setitem(sys.modules, "vel", SimpleNamespace(Agent=Agent))
    checked = asyncio.run(vel_explainer({"provider": "openai", "model": "test"})(rec))
    assert len(calls) == 2 and check(checked, rec) == []


def test_bundles_prefer_full_spend(conn):
    """Rank fill wins first; only a missed goal permits combination fallback."""
    slots = Slots(client_name="QSR", vertical="QSR", kpi="ctr", geo="US", budget=40_000)
    actual = recommend(conn, slots)
    expected = truth.best_bundle(truth.TruthSlots("QSR", "QSR", "ctr", "US", 40_000))
    assert [(line.product_id, round(line.spend, 2)) for line in actual.plan.lines] == [
        (line.product_id, round(line.spend, 2)) for line in expected.lines
    ]
    assert [line.product_id for line in actual.plan.lines] == ["P004", "P003", "P006"]
    assert actual.plan.total_spend == expected.total_spend == 40_000
    assert round(actual.plan.blended_kpi * 100, 3) == round(expected.blended_kpi * 100, 3) == 1.071
    # Existing sample contracts remain governed by the independent oracle after max_lines=3.
    for sample in truth.SAMPLE_SLOTS.values():
        sample_slots = _slots(sample)
        sample_actual = recommend(conn, sample_slots)
        sample_expected, _ = truth.recommend(sample)
        assert [line.product_id for line in sample_actual.plan.lines] == [line.product_id for line in sample_expected.lines]

    # Rule 4: Finance rank-fill misses this goal, so (and only so) the best
    # combination fallback supplies the plan.
    fallback_truth = truth.TruthSlots("Finance", "Finance", "ctr", "US", 25_000, 800_000)
    fallback_slots = _slots(fallback_truth)
    fallback_candidates = select_products(conn, fallback_slots, compute_benchmarks(conn, fallback_slots.vertical))
    fallback_scale = check_scale(conn, fallback_slots, fallback_candidates, InventoryPolicy())
    assert rank_fill(
        fallback_candidates,
        {item.product_id: item for item in fallback_scale},
        fallback_slots,
        InventoryPolicy(),
        SelectionPolicy(),
    ) is None
    fallback_actual = recommend(conn, fallback_slots)
    fallback_expected = truth.best_bundle(fallback_truth)
    assert [line.product_id for line in fallback_actual.plan.lines] == [line.product_id for line in fallback_expected.lines] == ["P003", "P006"]
    assert fallback_actual.plan.meets_goal is True


def test_plans_report_expected_outcomes_and_cost(conn):
    """The card's delivery comparison: outcomes = impressions x KPI, cost per click (CTR) or per
    1,000 viewable (in-view), recomputed from the raw-data oracle; the bundle not chosen carries
    its own totals. Shown for comparison only: the pick is unchanged (see the tests above)."""
    for name, sample in truth.SAMPLE_SLOTS.items():
        expected_plan, _ = truth.recommend(sample)
        actual = recommend(conn, _slots(sample)).plan
        outcomes = sum(line.impressions * line.kpi_value for line in expected_plan.lines)
        per = 1000 if sample.kpi == "in_view_rate" else 1
        assert isclose(actual.expected_outcomes, outcomes, rel_tol=1e-9), name
        assert isclose(actual.cost_per_outcome, expected_plan.total_spend / outcomes * per, rel_tol=1e-9), name
        assert isclose(sum(line.expected_outcomes for line in actual.lines), outcomes, rel_tol=1e-9), name

    acme = recommend(conn, Slots(client_name="Acme Shoes", vertical="Retail", kpi="ctr", geo="US", budget=30_000))
    assert [line.product_name for line in acme.plan.lines] == ["Premium Takeover"]
    assert round(acme.plan.expected_outcomes) == 8_493 and round(acme.plan.cost_per_outcome, 2) == 3.53
    bundle = next(a for a in acme.alternatives if a.kind == "bundle")
    assert [line.product_id for line in bundle.plan.lines] == bundle.product_ids == ["P003", "P006"]
    assert round(bundle.plan.expected_outcomes) == 23_592 and round(bundle.plan.cost_per_outcome, 2) == 1.27
    assert all(a.plan is None for a in acme.alternatives if a.kind != "bundle")

    # Payloads saved before these fields existed still load (old chats, the example chats).
    old = acme.model_dump(mode="json")
    del old["plan"]["expected_outcomes"], old["plan"]["cost_per_outcome"]
    for line in old["plan"]["lines"]:
        del line["expected_outcomes"]
    for alt in old["alternatives"]:
        del alt["plan"]
    restored = type(acme).model_validate(old)
    assert restored.plan.expected_outcomes == 0.0 and restored.plan.cost_per_outcome is None
