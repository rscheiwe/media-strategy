"""The deterministic steps of the recommendation workflow (brief requirement 8).

Pure functions over the read-only case database; no LLM. Requirements 3, 4 and 5 map to
`compute_benchmarks`, `select_products` and `check_scale`; `recommend` chains every step and
returns the structured Recommendation with a templated rationale. The mesh DAG (dag.py) runs
these same functions as nodes.
"""

from __future__ import annotations

import itertools
import math
import re
import sqlite3

from .domain import (
    GEOS,
    KPI_LABELS,
    KPIS,
    REQUIRED_SLOTS,
    VERTICALS,
    Alternative,
    Benchmark,
    Candidate,
    Plan,
    PlanLine,
    Rationale,
    Recommendation,
    Rejection,
    ScaleCheck,
    Slots,
    Tradeoff,
)
from .policy import InventoryPolicy, SelectionPolicy, policy_record

NEAR_TIE = 0.005  # half a percentage point
THIN_MARGIN = 0.05  # capacity within 5% of the impressions bought


# --- validation -------------------------------------------------------------------------------


def slot_valid(name: str, value) -> bool:
    """Step 0 (req 2, 7, 8.1): is this one detail present and allowed?

    Budget must be a positive number; vertical, KPI and geo must be one of the brief's options
    (so "LATAM" is invalid, not just missing).
    """
    if name == "budget":
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0
    options = {"vertical": VERTICALS, "kpi": KPIS, "geo": GEOS}[name]
    return value in options


_BRIEF_ADVERTISER = re.compile(r"([A-Z][\w&'.-]*(?: [A-Z][\w&'.-]*)*) is an? ")


def advertiser_names(conn: sqlite3.Connection) -> list[str]:
    """Step 0 helper: advertiser names in the sample client briefs ("Acme Shoes is a ...").

    The case data has no advertiser list (campaign history is by product and vertical only),
    so these are suggestions for the form's client field, not a closed set.
    """
    names = []
    for (text,) in conn.execute("SELECT text FROM client_briefs ORDER BY rowid"):
        m = _BRIEF_ADVERTISER.match(text or "")
        if m and m.group(1) not in names:
            names.append(m.group(1))
    return names


def missing_slots(slots: Slots) -> list[str]:
    """Step 0 (req 2, 7, 8.1-8.2): which required details are absent or invalid.

    Returned in a fixed order (vertical, kpi, geo, budget). A non-empty list becomes the form's
    missing fields, and nothing below runs.
    """
    return [name for name in REQUIRED_SLOTS if not slot_valid(name, getattr(slots, name))]


# --- requirement 3: benchmarks ---------------------------------------------------------------


def compute_benchmarks(conn: sqlite3.Connection, vertical: str) -> list[Benchmark]:
    """Step 1 (req 3, 8.3): how good each product is for this vertical.

    CTR = total clicks / total impressions and in-view rate = total viewable / total impressions,
    summed over every campaign_history line item for the product and vertical (ratio of sums,
    not an average of per-line rates). E.g. Retail: Commerce Connect CTR 1.23%.
    """
    rows = conn.execute(
        "SELECT product_id, product_name, cpm, line_items, impressions, clicks, viewable_impressions,"
        " ctr, in_view_rate FROM benchmarks WHERE vertical = ? ORDER BY product_id",
        (vertical,),
    ).fetchall()
    return [Benchmark(**dict(row)) for row in rows]


# --- requirement 4: select and estimate ------------------------------------------------------


def affordable_imps(budget: float, cpm: float) -> int:
    """Step 2 (req 8.4): impressions the budget buys at a CPM: floor(budget / CPM x 1000).

    E.g. $30,000 at a $15 CPM buys 2,000,000.
    """
    return int(math.floor(budget / cpm * 1000 + 1e-9))


def select_products(conn: sqlite3.Connection, slots: Slots, benchmarks: list[Benchmark]) -> list[Candidate]:
    """Step 2 (req 4, 8.4): every product ranked by the client's KPI, with what the budget buys.

    Attaches CPM, KPI value, KPI rank (1 = best) and affordable impressions. Nothing is removed
    here; viability is decided in steps 3-4.
    """
    by_kpi = sorted(benchmarks, key=lambda b: (-getattr(b, slots.kpi), b.product_id))
    return [
        Candidate(
            product_id=b.product_id,
            product_name=b.product_name,
            cpm=b.cpm,
            kpi_value=getattr(b, slots.kpi),
            kpi_rank=rank,
            affordable_imps=affordable_imps(slots.budget, b.cpm),
        )
        for rank, b in enumerate(by_kpi, start=1)
    ]


# --- requirement 5: scale ---------------------------------------------------------------------


def check_scale(
    conn: sqlite3.Connection, slots: Slots, candidates: list[Candidate], policy: InventoryPolicy
) -> list[ScaleCheck]:
    """Step 3 (req 5, 8.5): is there enough forecast inventory for the vertical and geo?

    Usable inventory comes from the InventoryPolicy (default: forecast x confidence, our rule for
    inventory_risk). absorbs_budget = usable inventory covers what the budget buys. E.g. Acme,
    Commerce Connect: 1,720,000 x 0.83 = 1,427,600 < 2,000,000, so it can't absorb the budget.
    """
    rows = {
        r["product_id"]: (int(r["available_imps"]), float(r["inventory_risk"]))
        for r in conn.execute(
            "SELECT product_id, available_imps, inventory_risk FROM inventory WHERE vertical = ? AND geo = ?",
            (slots.vertical, slots.geo),
        )
    }
    checks = []
    for c in candidates:
        available, confidence = rows.get(c.product_id, (0, 0.0))
        capacity = policy.capacity(available, confidence)
        checks.append(
            ScaleCheck(
                product_id=c.product_id,
                available_imps=available,
                confidence=confidence,
                confidence_tier=policy.tier(confidence),
                capacity=capacity,
                absorbs_budget=capacity >= c.affordable_imps * policy.min_absorption,
                deliverable_imps=min(c.affordable_imps, capacity),
            )
        )
    return checks


# --- viability ---------------------------------------------------------------------------------


def assess(
    candidates: list[Candidate], scale: list[ScaleCheck], slots: Slots, policy: InventoryPolicy
) -> dict[str, list[Rejection]]:
    """Step 4: can each product carry the whole brief alone? Returns its reasons why not.

    Each reason is a code with its numbers: INVENTORY_BELOW_BUDGET (from check_scale's
    absorbs_budget), BUDGET_BELOW_GOAL, INVENTORY_BELOW_GOAL, and BELOW_MIN_CONFIDENCE (threshold
    policy only). An empty list means viable alone.
    """
    by_id = {s.product_id: s for s in scale}
    goal = slots.impression_goal or 0
    out: dict[str, list[Rejection]] = {}
    for c in candidates:
        s = by_id[c.product_id]
        reasons: list[Rejection] = []
        if policy.mode == "threshold" and s.confidence < policy.min_confidence:
            reasons.append(
                Rejection(code="BELOW_MIN_CONFIDENCE", detail={"confidence": s.confidence, "min": policy.min_confidence})
            )
        if not s.absorbs_budget:
            reasons.append(
                Rejection(
                    code="INVENTORY_BELOW_BUDGET",
                    detail={
                        "capacity": s.capacity,
                        "needed": c.affordable_imps,
                        "shortfall": max(0, c.affordable_imps - s.capacity),
                    },
                )
            )
        if goal and c.affordable_imps < goal:
            reasons.append(Rejection(code="BUDGET_BELOW_GOAL", detail={"affordable": c.affordable_imps, "goal": goal}))
        if goal and s.capacity < goal:
            reasons.append(Rejection(code="INVENTORY_BELOW_GOAL", detail={"capacity": s.capacity, "goal": goal}))
        out[c.product_id] = reasons
    return out


# --- planning ----------------------------------------------------------------------------------


def outcome_cost(spend: float, outcomes: float, kpi: str) -> float | None:
    """Step 5 helper: what one outcome costs: USD per click for CTR, per 1,000 viewable impressions
    for in-view rate. E.g. Acme's pick: $30,000 / 8,493 clicks = $3.53 per click.

    Shown next to the KPI rate for comparison; it never ranks anything (the brief ranks by the KPI).
    """
    if not outcomes:
        return None
    return spend / outcomes * (1000 if kpi == "in_view_rate" else 1)


def _line(c: Candidate, s: ScaleCheck, spend: float, policy: InventoryPolicy) -> PlanLine:
    """Step 5 helper: one plan line: spend, impressions bought, KPI, confidence, low-confidence flag,
    and the outcomes those impressions are expected to deliver."""
    impressions = affordable_imps(spend, c.cpm)
    return PlanLine(
        product_id=c.product_id,
        product_name=c.product_name,
        spend=round(spend, 2),
        impressions=impressions,
        kpi_value=c.kpi_value,
        confidence=s.confidence,
        low_confidence=s.confidence < policy.low_confidence,
        expected_outcomes=impressions * c.kpi_value,
    )


def _finish(kind: str, lines: list[PlanLine], slots: Slots) -> Plan:
    """Step 5 helper: plan totals: spend, unspent budget, impressions, blended KPI, goal met, and
    expected outcomes with their cost."""
    total_imps = sum(line.impressions for line in lines)
    spend = round(sum(line.spend for line in lines), 2)
    outcomes = sum(line.expected_outcomes for line in lines)
    blended = sum(line.impressions * line.kpi_value for line in lines) / total_imps if total_imps else 0.0
    goal = slots.impression_goal
    return Plan(
        kind=kind,
        lines=lines,
        total_spend=spend,
        unspent=round(max(0.0, slots.budget - spend), 2),
        total_impressions=total_imps,
        blended_kpi=blended,
        meets_goal=None if not goal else total_imps >= goal,
        expected_outcomes=outcomes,
        cost_per_outcome=outcome_cost(spend, outcomes, slots.kpi),
    )


def _bundle(combo, scale_by_id, slots: Slots, inv: InventoryPolicy, sel: SelectionPolicy) -> Plan | None:
    """Step 5 helper: fill one combination of products in KPI order, each up to its usable inventory.

    Returns None if any line would be under the minimum line spend or the goal isn't met. Budget
    that won't fit stays unspent (CineVerse: $1,835.44).
    """
    remaining = float(slots.budget)
    lines = []
    for c in sorted(combo, key=lambda c: (-c.kpi_value, c.product_id)):
        s = scale_by_id[c.product_id]
        spend = round(min(remaining, s.capacity * c.cpm / 1000), 2)
        if spend < sel.min_line_spend:
            return None
        lines.append(_line(c, s, spend, inv))
        remaining -= spend
    plan = _finish("bundle", lines, slots)
    if slots.impression_goal and plan.total_impressions < slots.impression_goal:
        return None
    return plan


def rank_fill(candidates, scale_by_id, slots: Slots, inv: InventoryPolicy, sel: SelectionPolicy) -> Plan | None:
    """Step 5 helper: fill the budget in KPI rank order, each product up to its usable inventory.

    The #1 product takes what it can, then #2, then #3 (up to max_lines), until the budget is
    spent; a product whose share would be under min_line_spend is skipped. Budget left under
    min_line_spend stays unspent. None if that gives fewer than 2 lines or misses the goal.
    """
    remaining = float(slots.budget)
    lines = []
    for c in sorted(candidates, key=lambda c: (-c.kpi_value, c.product_id)):
        if remaining < sel.min_line_spend or len(lines) == sel.max_lines:
            break
        s = scale_by_id[c.product_id]
        spend = round(min(remaining, s.capacity * c.cpm / 1000), 2)
        if spend < sel.min_line_spend:
            continue  # too little inventory to be worth a line
        lines.append(_line(c, s, spend, inv))
        remaining -= spend
    if len(lines) < 2:
        return None
    plan = _finish("bundle", lines, slots)
    if slots.impression_goal and plan.total_impressions < slots.impression_goal:
        return None
    return plan


def best_bundle(candidates, scale, slots, inv: InventoryPolicy, sel: SelectionPolicy) -> Plan | None:
    """Step 5 (req "small bundle", up to max_lines products). See README.md, "Selection rules".

    Rule: fill the budget in KPI rank order (rank_fill), which is what "rank viable products by
    the requested KPI" means for a bundle. Only if that misses the impression goal: the best
    combination that meets it, by brute force (spends the budget first, then blended KPI, then
    fewer lines).
    """
    scale_by_id = {s.product_id: s for s in scale}
    if plan := rank_fill(candidates, scale_by_id, slots, inv, sel):
        return plan
    bundles = []
    for size in range(2, sel.max_lines + 1):
        for combo in itertools.combinations(candidates, size):
            plan = _bundle(combo, scale_by_id, slots, inv, sel)
            if plan:
                bundles.append(plan)
    if not bundles:
        return None

    def rank(p: Plan):
        leftover = sel.prefer_full_spend and p.unspent >= sel.min_line_spend
        return (leftover, -p.blended_kpi, len(p.lines), -p.total_impressions)

    return sorted(bundles, key=rank)[0]


def no_plan(slots: Slots) -> Plan:
    """Step 5: nothing viable: the whole budget unspent, and a set goal is not met."""
    return Plan(kind="none", unspent=round(float(slots.budget), 2), meets_goal=False if slots.impression_goal else None)


def build_plan(candidates, scale, rejections, slots, inv: InventoryPolicy, sel: SelectionPolicy) -> tuple[Plan, Plan | None]:
    """Step 5 (req 8.6): choose the plan under the selection policy.

    single_first (default): the highest-KPI product that can carry the brief alone (Acme: Premium
    Takeover; Commerce Connect and Attention Builder rank higher but fail step 4); otherwise the
    best bundle; otherwise no plan. kpi_max: whichever single or bundle has the highest blended
    KPI. Also returns the best bundle, for comparison.
    """
    scale_by_id = {s.product_id: s for s in scale}
    viable = sorted((c for c in candidates if not rejections[c.product_id]), key=lambda c: (-c.kpi_value, c.product_id))
    if slots.budget < sel.min_line_spend:  # a single line below the minimum isn't bookable either
        viable = []
    singles = [_finish("single", [_line(c, scale_by_id[c.product_id], slots.budget, inv)], slots) for c in viable]
    bundle = best_bundle(candidates, scale, slots, inv, sel)
    if sel.mode == "kpi_max":
        options = singles + ([bundle] if bundle else [])
        if not options:
            return no_plan(slots), bundle
        return sorted(options, key=lambda p: (-p.blended_kpi, len(p.lines), -p.total_impressions))[0], bundle
    if singles:
        return singles[0], bundle
    if bundle:
        return bundle, bundle
    return no_plan(slots), None


# --- explanation material ---------------------------------------------------------------------


def alternatives(candidates, scale, rejections, plan: Plan, bundle: Plan | None, sel: SelectionPolicy) -> list[Alternative]:
    """Step 6 (req 6, 8.7, 9.6): what wasn't chosen, for the explanation.

    Every product ranked above the pick, with its reasons and deltas (rejected leaders); the
    runner-up; and the best bundle if it beats the pick by bundle_uplift_to_surface (Acme:
    Commerce Connect + Premium Takeover, +29.65% CTR).
    """
    scale_by_id = {s.product_id: s for s in scale}
    in_plan = {line.product_id for line in plan.lines}
    pick_kpi = plan.blended_kpi
    out: list[Alternative] = []
    for c in sorted(candidates, key=lambda c: c.kpi_rank):
        if c.product_id in in_plan or (plan.kind != "none" and c.kpi_value <= pick_kpi):
            continue
        s = scale_by_id[c.product_id]
        shortfall = max(0, c.affordable_imps - s.capacity)
        out.append(
            Alternative(
                product_ids=[c.product_id],
                kind="rejected_leader",
                kpi_value=c.kpi_value,
                reasons=[r.code for r in rejections[c.product_id]],
                delta={
                    "kpi_points": round((c.kpi_value - pick_kpi) * 100, 4),
                    "impression_shortfall": shortfall,
                    "unabsorbed_spend": round(shortfall * c.cpm / 1000, 2),
                },
            )
        )
    if plan.kind == "single":
        runners = sorted(
            (c for c in candidates if not rejections[c.product_id] and c.product_id not in in_plan),
            key=lambda c: (-c.kpi_value, c.product_id),
        )
        if runners:
            r = runners[0]
            out.append(
                Alternative(
                    product_ids=[r.product_id],
                    kind="runner_up",
                    kpi_value=r.kpi_value,
                    delta={"kpi_points": round((r.kpi_value - pick_kpi) * 100, 4)},
                )
            )
    if bundle and plan.kind != "bundle" and pick_kpi and bundle.blended_kpi >= pick_kpi * (1 + sel.bundle_uplift_to_surface):
        out.append(
            Alternative(
                product_ids=[line.product_id for line in bundle.lines],
                kind="bundle",
                kpi_value=bundle.blended_kpi,
                delta={
                    "kpi_points": round((bundle.blended_kpi - pick_kpi) * 100, 4),
                    "uplift_pct": round((bundle.blended_kpi / pick_kpi - 1) * 100, 2),
                },
                plan=bundle,
            )
        )
    return out


def tradeoffs(candidates, scale, plan: Plan, alts: list[Alternative], slots: Slots, sel: SelectionPolicy | None = None) -> list[Tradeoff]:
    """Step 6 (req 6, 8.7): the tradeoffs, each a code with its numbers.

    KPI_LEADER_NOT_VIABLE, BUNDLE_UPLIFT, NEAR_TIE (runner-up within 0.5 points), LOW_CONFIDENCE_LINE,
    THIN_MARGIN (usable inventory within 5% of the buy; Acme 0.32%), UNSPENT_BUDGET, GOAL_MARGIN,
    NO_VIABLE_PLAN. explain.py turns them into sentences for the model.
    """
    scale_by_id = {s.product_id: s for s in scale}
    by_id = {c.product_id: c for c in candidates}
    out: list[Tradeoff] = []
    leader = min(candidates, key=lambda c: c.kpi_rank)
    in_plan = {line.product_id for line in plan.lines}
    if plan.kind == "none":
        out.append(Tradeoff(code="NO_VIABLE_PLAN", facts={"budget": slots.budget, "goal": slots.impression_goal}))
    if leader.product_id not in in_plan:
        rejected = next((a for a in alts if a.product_ids == [leader.product_id]), None)
        out.append(
            Tradeoff(
                code="KPI_LEADER_NOT_VIABLE",
                facts={"product_id": leader.product_id, "reasons": rejected.reasons if rejected else []},
            )
        )
    for a in alts:
        if a.kind == "bundle":
            out.append(Tradeoff(code="BUNDLE_UPLIFT", facts={"product_ids": a.product_ids, **a.delta}))
        if a.kind == "runner_up" and abs(a.delta.get("kpi_points", 1)) < NEAR_TIE * 100:
            out.append(Tradeoff(code="NEAR_TIE", facts={"product_id": a.product_ids[0], **a.delta}))
    for line in plan.lines:
        if line.low_confidence:
            out.append(Tradeoff(code="LOW_CONFIDENCE_LINE", facts={"product_id": line.product_id, "confidence": line.confidence}))
    if plan.kind == "single":
        line = plan.lines[0]
        cap = scale_by_id[line.product_id].capacity
        margin = (cap - line.impressions) / line.impressions if line.impressions else 0
        if margin < THIN_MARGIN:
            out.append(Tradeoff(code="THIN_MARGIN", facts={"product_id": line.product_id, "margin_pct": round(margin * 100, 2)}))
    if plan.kind != "none" and plan.unspent > 0:
        sel = sel or SelectionPolicy()
        # Why it's unspent: too little for another line, the bundle size limit, or no inventory left.
        if plan.unspent < sel.min_line_spend:
            why = "min_line_spend"
        elif plan.kind == "bundle" and len(plan.lines) >= sel.max_lines:
            why = "line_limit"
        else:
            why = "no_inventory"
        out.append(Tradeoff(code="UNSPENT_BUDGET", facts={"unspent": plan.unspent, "reason": why,
                                                          "min_line_spend": sel.min_line_spend, "max_lines": sel.max_lines}))
    if slots.impression_goal and plan.kind != "none":
        out.append(
            Tradeoff(
                code="GOAL_MARGIN",
                facts={"goal": slots.impression_goal, "margin_pct": round((plan.total_impressions / slots.impression_goal - 1) * 100, 2)},
            )
        )
    del by_id
    return out


# --- display strings (for the templated rationale) ----------------------------------


def pct(x: float) -> str:
    return f"{x:.2%}"


def money(x: float) -> str:
    return f"${x:,.0f}" if float(x).is_integer() else f"${x:,.2f}"


def build_display(slots: Slots, candidates, scale, plan: Plan, alts: list[Alternative]) -> dict[str, str]:
    """Step 7 (fallback only): pre-formatted strings for template_rationale."""
    scale_by_id = {s.product_id: s for s in scale}
    d: dict[str, str] = {"budget": money(slots.budget)}
    if slots.impression_goal:
        d["goal"] = f"{slots.impression_goal:,}"
    for c in candidates:
        s = scale_by_id[c.product_id]
        p = c.product_id
        d[f"{p}.kpi"] = pct(c.kpi_value)
        d[f"{p}.cpm"] = money(c.cpm)
        d[f"{p}.affordable"] = f"{c.affordable_imps:,}"
        d[f"{p}.available"] = f"{s.available_imps:,}"
        d[f"{p}.capacity"] = f"{s.capacity:,}"
        d[f"{p}.confidence"] = f"{s.confidence:.2f}"
    for i, line in enumerate(plan.lines):
        d[f"line{i}.spend"] = money(line.spend)
        d[f"line{i}.impressions"] = f"{line.impressions:,}"
    if plan.kind != "none":
        d["plan.total_impressions"] = f"{plan.total_impressions:,}"
        d["plan.blended_kpi"] = pct(plan.blended_kpi)
        d["plan.total_spend"] = money(plan.total_spend)
        d["plan.unspent"] = money(plan.unspent)
    for a in alts:
        if a.kind == "bundle":
            d["bundle.blended_kpi"] = pct(a.kpi_value)
            d["bundle.uplift_pct"] = f"{a.delta['uplift_pct']:.1f}%"
    return d


# --- templated rationale ----------------------------------------------------------------------

REASON_TEXT = {
    "INVENTORY_BELOW_BUDGET": "its forecast capacity can't absorb the full budget",
    "BUDGET_BELOW_GOAL": "the budget buys fewer impressions than the goal",
    "INVENTORY_BELOW_GOAL": "its forecast capacity is below the impression goal",
    "BELOW_MIN_CONFIDENCE": "its forecast confidence is below the policy minimum",
}


def template_rationale(slots: Slots, candidates, scale, plan: Plan, alts: list[Alternative], d: dict[str, str]) -> str:
    """Step 7 (fallback only): a fixed-wording rationale, used if the model call fails."""
    names = {c.product_id: c.product_name for c in candidates}
    kpi = KPI_LABELS[slots.kpi]
    if plan.kind == "none":
        return (
            f"No product or small bundle can deliver this brief within {d['budget']} under the current "
            "inventory policy. Consider a larger budget, a lower impression goal or a different geo."
        )
    if plan.kind == "single":
        line = plan.lines[0]
        text = f"{line.product_name} is the best fit: the highest {kpi} ({d[line.product_id + '.kpi']}) among products that can carry the whole brief."
    else:
        parts = " + ".join(f"{line.product_name} ({d[f'line{i}.spend']})" for i, line in enumerate(plan.lines))
        text = f"No single product can carry the brief, so the plan bundles {parts} for a blended {kpi} of {d['plan.blended_kpi']}."
    leaders = [a for a in alts if a.kind == "rejected_leader"]
    if leaders:
        a = leaders[0]
        reasons = " and ".join(REASON_TEXT[r] for r in a.reasons) or "it isn't viable"
        text += f" {names[a.product_ids[0]]} has a higher {kpi} ({d[a.product_ids[0] + '.kpi']}) but {reasons}."
    return text


# --- the whole chain --------------------------------------------------------------------------


def recommend(
    conn: sqlite3.Connection,
    slots: Slots,
    inventory_policy: InventoryPolicy | None = None,
    selection_policy: SelectionPolicy | None = None,
) -> Recommendation:
    """Step 7 (req 8.8, 9): run steps 0-6 in order and return the structured Recommendation.

    The card renders it; the rationale is the template until the explain step (dag.py) replaces it
    with the model's text. The mesh DAG runs the same functions as nodes.
    """
    inv = inventory_policy or InventoryPolicy()
    sel = selection_policy or SelectionPolicy()
    missing = missing_slots(slots)
    if missing:
        raise ValueError(f"missing or invalid slots: {missing}")
    bench = compute_benchmarks(conn, slots.vertical)
    cands = select_products(conn, slots, bench)
    scale = check_scale(conn, slots, cands, inv)
    rejections = assess(cands, scale, slots, inv)
    plan, bundle = build_plan(cands, scale, rejections, slots, inv, sel)
    alts = alternatives(cands, scale, rejections, plan, bundle, sel)
    trades = tradeoffs(cands, scale, plan, alts, slots, sel)
    display = build_display(slots, cands, scale, plan, alts)
    rec = Recommendation(
        requirements=slots,
        benchmarks=bench,
        candidates=cands,
        scale=scale,
        rejections=rejections,
        plan=plan,
        alternatives=alts,
        tradeoffs=trades,
        display=display,
        rationale=Rationale(text=template_rationale(slots, cands, scale, plan, alts, display)),
        policy=policy_record(inv, sel),
    )
    from .explain import template_rejected  # explain builds on steps' outputs; imported here

    rec.rationale.rejected = template_rejected(rec)  # 9.6
    return rec
