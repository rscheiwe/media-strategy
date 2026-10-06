"""The recommendation workflow as a mesh DAG (brief requirement 8).

    validate ─┬─ missing → form                                      (human in the loop)
              └─ ok → benchmarks → select → scale → viability → plan → alternatives → explain

Tool nodes only: each wraps a pure step from steps.py and keeps its results in mesh state;
the node's return value is a short summary, which the service turns into a stage row. The
explain node calls the injected explainer (an LLM), falling back to the template. The graph is built
per run with closures over the read-only connection, the policies and the explainer, so no
connection or callable ever goes into mesh state.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from mesh import StateGraph

from . import steps
from .domain import KPI_LABELS, Rationale, Recommendation, Slots
from .explain import Explainer, template_rejected, to_rationale
from .form import build_form
from .policy import InventoryPolicy, SelectionPolicy, policy_record

STAGES = ("validate", "benchmarks", "select", "scale", "viability", "plan", "alternatives", "explain")
STAGE_LABELS = {
    "validate": "Check the brief",
    "benchmarks": "Benchmark products",
    "select": "Estimate impressions",
    "scale": "Check inventory",
    "viability": "Check viability",
    "plan": "Rank and plan",
    "alternatives": "Compare alternatives",
    "explain": "Write the rationale",
}


def build_dag(
    conn: sqlite3.Connection,
    inv: InventoryPolicy,
    sel: SelectionPolicy,
    explainer: Explainer | None,
    *,
    form_id: str,
):
    def validate(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        missing = steps.missing_slots(slots)
        # Inferred-but-unconfirmed vertical/KPI count as missing: asked, pre-selected.
        missing += [s for s in state.get("unconfirmed", []) if s not in missing]
        state["missing"] = missing
        confirm = state.get("confirm") is not None and not missing
        summary = "Confirm the details" if confirm else "All details present" if not missing else f"Missing: {', '.join(missing)}"
        return {"missing": missing, "confirm": confirm, "summary": summary}

    def form(state: dict[str, Any]) -> dict[str, Any]:
        out = build_form(
            Slots(**state["slots"]),
            state["missing"],
            form_id=form_id,
            inferred=[*state.get("unconfirmed", []), *(state.get("confirm") or [])],
            confirm=state.get("confirm") is not None,
            client_suggestions=steps.advertiser_names(conn),
        )
        state["form"] = out
        return {"summary": "Asked for the missing details"}

    def benchmarks(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        bench = steps.compute_benchmarks(conn, slots.vertical)
        state["benchmarks"] = bench
        return {"summary": f"{len(bench)} products benchmarked for {slots.vertical}"}

    def select(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        cands = steps.select_products(conn, slots, state["benchmarks"])
        state["candidates"] = cands
        leader = cands[0]
        return {"summary": f"{leader.product_name} leads on {KPI_LABELS[slots.kpi]}"}

    def scale(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        checks = steps.check_scale(conn, slots, state["candidates"], inv)
        state["scale"] = checks
        short = sum(1 for c in checks if not c.absorbs_budget)
        return {"summary": f"{len(checks)} checked in {slots.geo}; {short} can't absorb the budget"}

    def viability(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        rejections = steps.assess(state["candidates"], state["scale"], slots, inv)
        state["rejections"] = rejections
        viable = sum(1 for r in rejections.values() if not r)
        return {"summary": f"{viable} viable on their own"}

    def plan(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        chosen, bundle = steps.build_plan(state["candidates"], state["scale"], state["rejections"], slots, inv, sel)
        state["plan"], state["bundle"] = chosen, bundle
        if chosen.kind == "none":
            return {"summary": "No viable plan"}
        names = " + ".join(line.product_name for line in chosen.lines)
        return {"summary": f"{'Bundle' if chosen.kind == 'bundle' else 'Pick'}: {names}"}

    def alternatives(state: dict[str, Any]) -> dict[str, Any]:
        slots = Slots(**state["slots"])
        alts = steps.alternatives(state["candidates"], state["scale"], state["rejections"], state["plan"], state["bundle"], sel)
        trades = steps.tradeoffs(state["candidates"], state["scale"], state["plan"], alts, slots, sel)
        display = steps.build_display(slots, state["candidates"], state["scale"], state["plan"], alts)
        rec = Recommendation(
            requirements=slots,
            benchmarks=state["benchmarks"],
            candidates=state["candidates"],
            scale=state["scale"],
            rejections=state["rejections"],
            plan=state["plan"],
            alternatives=alts,
            tradeoffs=trades,
            display=display,
            rationale=Rationale(
                text=steps.template_rationale(slots, state["candidates"], state["scale"], state["plan"], alts, display)
            ),
            policy=policy_record(inv, sel),
        )
        rec.rationale.rejected = template_rejected(rec)  # 9.6, until the explain step improves it
        state["recommendation"] = rec
        rejected = sum(1 for a in alts if a.kind == "rejected_leader")
        return {"summary": f"{rejected} higher-ranked products explained; {len(trades)} tradeoffs"}

    async def explain(state: dict[str, Any]) -> dict[str, Any]:
        rec: Recommendation = state["recommendation"]
        if explainer is None or rec.plan.kind == "none":
            return {"summary": "Templated rationale"}
        try:
            out = await explainer(rec)
        except Exception:  # noqa: BLE001 - any explainer failure falls back to the template
            return {"summary": "Templated rationale (explainer failed)"}
        if not out:
            return {"summary": "Templated rationale (explainer returned nothing)"}
        if isinstance(out, str):  # a plain-text explainer: the rationale paragraph only
            rec.rationale = Rationale(text=out, source="llm", rejected=rec.rationale.rejected)
        else:
            rec.rationale = to_rationale(out, rec)
        return {"summary": "Rationale written"}

    nodes = {
        "validate": validate,
        "form": form,
        "benchmarks": benchmarks,
        "select": select,
        "scale": scale,
        "viability": viability,
        "plan": plan,
        "alternatives": alternatives,
        "explain": explain,
    }
    graph = StateGraph()
    for name, fn in nodes.items():
        graph.add_node(name, fn, node_type="tool")
    graph.set_entry_point("validate")
    graph.add_conditional_edges(
        "validate",
        lambda out: "missing" if out["missing"] or out.get("confirm") else "ok",
        {"missing": "form", "ok": "benchmarks"},
    )
    graph.add_sequence(
        ["benchmarks", "select", "scale", "viability", "plan", "alternatives", "explain"]
    )
    return graph.compile()
