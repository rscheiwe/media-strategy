"""The recommendation workflow's steps, each runnable on its own (brief requirements 3, 4, 5).

Thin tools over the same functions the DAG runs (workflow/steps.py), so a single-step answer
("What are the Retail benchmarks?", "Is there scale in APAC for $20k?") uses exactly the
figures and the inventory rule the full recommendation would. No logic lives here: each
handler validates its inputs, calls the step and returns its rows.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from typing import Any

from vel.tools.registry import ToolSpec  # type: ignore

from chat_api.casedata import connect_readonly, ensure_case_db
from chat_api.workflow import steps
from chat_api.workflow.domain import GEOS, KPIS, VERTICALS, Slots
from chat_api.workflow.policy import inventory_policy


def _invalid(**values: Any) -> dict | None:
    bad = [f"{name}={value!r}" for name, value in values.items() if not steps.slot_valid(name, value)]
    if not bad:
        return None
    return {"error": f"Invalid {', '.join(bad)}. Verticals: {', '.join(VERTICALS)}; KPIs: {', '.join(KPIS)}; geos: {', '.join(GEOS)}; budget > 0."}


def _run(fn) -> dict:
    try:
        with closing(connect_readonly(ensure_case_db())) as conn:
            return fn(conn)
    except (FileNotFoundError, sqlite3.Error) as exc:
        return {"error": f"Case data unavailable: {exc}"}


# --- requirement 3 ------------------------------------------------------------------------------

def _get_benchmarks(inp: dict, ctx: dict) -> dict:
    vertical = inp.get("vertical")
    if err := _invalid(vertical=vertical):
        return err
    return _run(lambda conn: {
        "vertical": vertical,
        "method": "ratio of sums over the vertical's campaign line items: CTR = clicks / impressions, in-view rate = viewable / impressions",
        "benchmarks": [b.model_dump() for b in steps.compute_benchmarks(conn, vertical)],
    })


GET_BENCHMARKS = ToolSpec(
    name="get_benchmarks",
    description=(
        "Workflow step 1: product performance benchmarks for one vertical from campaign history: "
        "per product, CTR and in-view rate (ratio of sums over its line items), with the totals they "
        "come from and the product's CPM. Use it for benchmark questions about a vertical."
    ),
    input_schema={
        "type": "object",
        "properties": {"vertical": {"type": "string", "enum": list(VERTICALS)}},
        "required": ["vertical"],
    },
    output_schema={"type": "object"},
    handler=_get_benchmarks,
    parallel_safe=True,
)


# --- requirement 4 ------------------------------------------------------------------------------

def _select_products(inp: dict, ctx: dict) -> dict:
    vertical, kpi, budget = inp.get("vertical"), inp.get("kpi"), inp.get("budget")
    if err := _invalid(vertical=vertical, kpi=kpi, budget=budget):
        return err
    slots = Slots(vertical=vertical, kpi=kpi, budget=float(budget))

    def run(conn):
        cands = steps.select_products(conn, slots, steps.compute_benchmarks(conn, vertical))
        return {"vertical": vertical, "kpi": kpi, "budget": budget, "products": [c.model_dump() for c in cands]}

    return _run(run)


SELECT_PRODUCTS = ToolSpec(
    name="select_products",
    description=(
        "Workflow step 2: every product ranked by the client's primary KPI for a vertical, with its "
        "CPM, KPI value, rank (1 = best) and the impressions the budget buys (budget / CPM x 1000). "
        "It doesn't check inventory: use check_scale for that, or run_recommendation for a full plan."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "vertical": {"type": "string", "enum": list(VERTICALS)},
            "kpi": {"type": "string", "enum": list(KPIS), "description": "ctr or in_view_rate"},
            "budget": {"type": "number", "description": "Budget in USD, e.g. 25000."},
        },
        "required": ["vertical", "kpi", "budget"],
    },
    output_schema={"type": "object"},
    handler=_select_products,
    parallel_safe=True,
)


# --- requirement 5 ------------------------------------------------------------------------------

def _check_scale(inp: dict, ctx: dict) -> dict:
    vertical, geo, budget = inp.get("vertical"), inp.get("geo"), inp.get("budget")
    if err := _invalid(vertical=vertical, geo=geo, budget=budget):
        return err
    # The ranking KPI doesn't affect scale; select_products needs one to build its rows.
    slots = Slots(vertical=vertical, kpi="ctr", geo=geo, budget=float(budget))
    policy = inventory_policy()

    def run(conn):
        cands = steps.select_products(conn, slots, steps.compute_benchmarks(conn, vertical))
        checks = {s.product_id: s for s in steps.check_scale(conn, slots, cands, policy)}
        return {
            "vertical": vertical, "geo": geo, "budget": budget,
            "inventory_policy": policy.describe(),
            "products": [
                {"product_id": c.product_id, "product_name": c.product_name, "cpm": c.cpm,
                 "impressions_budget_buys": c.affordable_imps,
                 **checks[c.product_id].model_dump(exclude={"product_id"})}
                for c in sorted(cands, key=lambda c: c.product_id)
            ],
        }

    return _run(run)


CHECK_SCALE = ToolSpec(
    name="check_scale",
    description=(
        "Workflow step 3: whether each product has enough inventory for a vertical, geo and budget. "
        "Per product: forecast available impressions, forecast confidence (the inventory_risk score; "
        "higher = more reliable) and its tier, usable inventory under the inventory policy, the "
        "impressions the budget buys, and whether usable inventory absorbs them."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "vertical": {"type": "string", "enum": list(VERTICALS)},
            "geo": {"type": "string", "enum": list(GEOS)},
            "budget": {"type": "number", "description": "Budget in USD, e.g. 25000."},
        },
        "required": ["vertical", "geo", "budget"],
    },
    output_schema={"type": "object"},
    handler=_check_scale,
    parallel_safe=True,
)

STEP_TOOLS = [GET_BENCHMARKS, SELECT_PRODUCTS, CHECK_SCALE]
