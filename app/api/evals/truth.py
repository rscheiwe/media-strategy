"""Independent, raw-CSV oracle for recommendation-workflow acceptance tests.

This module deliberately does not import ``chat_api`` or read ``case.db``.  It
implements the accepted workflow definitions directly over the four case-study
CSV files, using pandas only as the tabular calculation engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations
from math import floor
from pathlib import Path
from typing import Literal

import pandas as pd


ROOT = Path(__file__).resolve().parents[1] / "case_data"  # app/api/case_data
VALID_VERTICALS = frozenset({"Retail", "Finance", "Travel", "QSR", "Entertainment"})
VALID_KPIS = frozenset({"ctr", "in_view_rate"})
VALID_GEOS = frozenset({"US", "EMEA", "APAC"})


@dataclass(frozen=True)
class TruthInventoryPolicy:
    mode: Literal["adjusted", "raw", "threshold"] = "adjusted"
    min_confidence: float = 0.80
    low_confidence: float = 0.80
    headroom: float = 0.0
    min_absorption: float = 1.0

    def capacity(self, available: int, confidence: float) -> int:
        if self.mode == "adjusted":
            base = available * confidence
        elif self.mode == "raw":
            base = available
        elif self.mode == "threshold":
            base = available if confidence >= self.min_confidence else 0
        else:  # pragma: no cover - protects callers of the independent oracle
            raise ValueError(f"unknown inventory mode: {self.mode}")
        return floor(base * (1 - self.headroom))


@dataclass(frozen=True)
class TruthSelectionPolicy:
    mode: Literal["single_first", "kpi_max"] = "single_first"
    max_lines: int = 3
    min_line_spend: float = 2000.0
    prefer_full_spend: bool = True
    bundle_uplift_to_surface: float = 0.05


@dataclass(frozen=True)
class TruthSlots:
    client_name: str | None = None
    vertical: str | None = None
    kpi: str | None = None
    geo: str | None = None
    budget: float | None = None
    impression_goal: int | None = None


@dataclass(frozen=True)
class TruthLine:
    product_id: str
    product_name: str
    spend: float
    impressions: int
    kpi_value: float
    confidence: float


@dataclass(frozen=True)
class TruthPlan:
    kind: Literal["single", "bundle", "none"]
    lines: tuple[TruthLine, ...]
    total_spend: float
    unspent: float
    total_impressions: int
    blended_kpi: float | None
    meets_goal: bool | None


def raw_tables(root: Path = ROOT) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read only the root case-study CSV inputs, with stable numeric dtypes."""
    products = pd.read_csv(root / "product_catalog.csv")
    history = pd.read_csv(root / "campaign_history.csv")
    inventory = pd.read_csv(root / "inventory_forecaster.csv")
    return products, history, inventory


def missing_slots(slots: TruthSlots) -> list[str]:
    """Required-slot validation in the accepted, user-facing order."""
    missing: list[str] = []
    if slots.vertical not in VALID_VERTICALS:
        missing.append("vertical")
    if slots.kpi not in VALID_KPIS:
        missing.append("kpi")
    if slots.geo not in VALID_GEOS:
        missing.append("geo")
    if slots.budget is None or slots.budget <= 0:
        missing.append("budget")
    return missing


def benchmarks(vertical: str, root: Path = ROOT) -> pd.DataFrame:
    """Ratio-of-sums benchmarks, one row per catalog product for ``vertical``."""
    products, history, _ = raw_tables(root)
    grouped = (
        history.loc[history["vertical"].eq(vertical)]
        .groupby("product_id", as_index=False)
        .agg(
            line_items=("product_id", "size"),
            impressions=("impressions", "sum"),
            clicks=("clicks", "sum"),
            viewable_impressions=("viewable_impressions", "sum"),
        )
    )
    grouped["ctr"] = grouped["clicks"] / grouped["impressions"]
    grouped["in_view_rate"] = grouped["viewable_impressions"] / grouped["impressions"]
    return products.merge(grouped, on="product_id", how="left").sort_values("product_id").reset_index(drop=True)


def candidates(slots: TruthSlots, root: Path = ROOT) -> pd.DataFrame:
    """All catalog products, ranked by requested KPI, with floor affordable imps."""
    if missing_slots(slots):
        raise ValueError("complete valid slots are required")
    result = benchmarks(slots.vertical, root).copy()
    result["kpi_value"] = result[slots.kpi]
    result["kpi_rank"] = result["kpi_value"].rank(method="min", ascending=False).astype(int)
    result["affordable_imps"] = (slots.budget / result["cpm"] * 1000).map(floor).astype(int)
    return result.sort_values(["kpi_rank", "product_id"]).reset_index(drop=True)


def scale(slots: TruthSlots, policy: TruthInventoryPolicy = TruthInventoryPolicy(), root: Path = ROOT) -> pd.DataFrame:
    """Join candidates to requested inventory and apply the accepted capacity rule."""
    result = candidates(slots, root)
    _, _, inventory = raw_tables(root)
    inventory = inventory.loc[(inventory["vertical"] == slots.vertical) & (inventory["geo"] == slots.geo)].copy()
    result = result.merge(inventory, on="product_id", how="left", validate="one_to_one")
    result = result.rename(columns={"inventory_risk": "confidence"})
    result["capacity"] = [policy.capacity(int(a), float(c)) for a, c in zip(result.available_imps, result.confidence)]
    result["confidence_tier"] = pd.cut(
        result["confidence"], bins=[-float("inf"), 0.80, 0.90, float("inf")], labels=["low", "medium", "high"], right=False
    ).astype(str)
    result["absorbs_budget"] = result["capacity"] >= result["affordable_imps"] * policy.min_absorption
    result["deliverable_imps"] = result[["affordable_imps", "capacity"]].min(axis=1).astype(int)
    return result.sort_values(["kpi_rank", "product_id"]).reset_index(drop=True)


def rejection_codes(row: pd.Series, slots: TruthSlots, policy: TruthInventoryPolicy) -> tuple[str, ...]:
    """Typed failures for a single product under the shared definitions."""
    codes: list[str] = []
    if policy.mode == "threshold" and row.confidence < policy.min_confidence:
        codes.append("BELOW_MIN_CONFIDENCE")
    if row.capacity < row.affordable_imps * policy.min_absorption:
        codes.append("INVENTORY_BELOW_BUDGET")
    if slots.impression_goal is not None:
        if row.affordable_imps < slots.impression_goal:
            codes.append("BUDGET_BELOW_GOAL")
        if row.capacity < slots.impression_goal:
            codes.append("INVENTORY_BELOW_GOAL")
    return tuple(codes)


def viable_alone(row: pd.Series, slots: TruthSlots, policy: TruthInventoryPolicy) -> bool:
    return not rejection_codes(row, slots, policy)


def _bundle(rows: tuple[pd.Series, ...], slots: TruthSlots, selection: TruthSelectionPolicy) -> TruthPlan | None:
    """Fill KPI-ordered lines to capacity, enforcing the line-spend and goal rules."""
    remaining = float(slots.budget)
    lines: list[TruthLine] = []
    for row in rows:
        max_spend = float(row.capacity) * float(row.cpm) / 1000
        spend = min(remaining, max_spend)
        if spend < selection.min_line_spend:
            continue
        impressions = floor(spend / float(row.cpm) * 1000)
        # Allocation is rounded to cents first; impressions are then floored from
        # that allocation.  The tiny CPM remainder stays as spend, rather than
        # silently becoming unspent budget.
        actual_spend = round(spend, 2)
        lines.append(TruthLine(row.product_id, row.product_name, actual_spend, impressions, float(row.kpi_value), float(row.confidence)))
        remaining = round(remaining - actual_spend, 2)
    if not lines:
        return None
    total_spend = round(sum(line.spend for line in lines), 2)
    total_impressions = sum(line.impressions for line in lines)
    meets_goal = None if slots.impression_goal is None else total_impressions >= slots.impression_goal
    if slots.impression_goal is not None and not meets_goal:
        return None
    blended = sum(line.impressions * line.kpi_value for line in lines) / total_impressions
    return TruthPlan("bundle" if len(lines) > 1 else "single", tuple(lines), total_spend, round(float(slots.budget) - total_spend, 2), total_impressions, blended, meets_goal)


def _rank_fill(rows: list[pd.Series], slots: TruthSlots, selection: TruthSelectionPolicy) -> TruthPlan | None:
    """Independent rule 3: fill #1, then #2, then #3; skip undersized shares."""
    remaining = float(slots.budget)
    lines: list[TruthLine] = []
    for row in rows:
        if remaining < selection.min_line_spend or len(lines) == selection.max_lines:
            break
        spend = round(min(remaining, float(row.capacity) * float(row.cpm) / 1000), 2)
        if spend < selection.min_line_spend:
            continue
        lines.append(TruthLine(row.product_id, row.product_name, spend, floor(spend / float(row.cpm) * 1000), float(row.kpi_value), float(row.confidence)))
        remaining = round(remaining - spend, 2)
    if len(lines) < 2:
        return None
    total_spend = round(sum(line.spend for line in lines), 2)
    total_impressions = sum(line.impressions for line in lines)
    meets_goal = None if slots.impression_goal is None else total_impressions >= slots.impression_goal
    if slots.impression_goal is not None and not meets_goal:
        return None
    blended = sum(line.impressions * line.kpi_value for line in lines) / total_impressions
    return TruthPlan("bundle", tuple(lines), total_spend, round(float(slots.budget) - total_spend, 2), total_impressions, blended, meets_goal)


def best_bundle(slots: TruthSlots, inventory_policy: TruthInventoryPolicy = TruthInventoryPolicy(), selection_policy: TruthSelectionPolicy = TruthSelectionPolicy(), root: Path = ROOT) -> TruthPlan:
    """Rule 3 rank fill; only then rule 4's best-combination fallback."""
    rows = [row for _, row in scale(slots, inventory_policy, root).sort_values(["kpi_value", "product_id"], ascending=[False, True]).iterrows()]
    if plan := _rank_fill(rows, slots, selection_policy):
        return plan
    # A "bundle" is the fallback after no product can absorb the whole buy alone.
    # A bundle contains at least two lines; every allowed width must be considered.
    widths = range(2, selection_policy.max_lines + 1)
    plans = [plan for width in widths for plan in (_bundle(combo, slots, selection_policy) for combo in combinations(rows, width)) if plan is not None and len(plan.lines) == width]
    if not plans:
        return TruthPlan("none", (), 0.0, round(float(slots.budget), 2), 0, None, None if slots.impression_goal is None else False)
    return min(
        plans,
        key=lambda p: (
            selection_policy.prefer_full_spend and p.unspent >= selection_policy.min_line_spend,
            -p.blended_kpi,
            len(p.lines),
            -p.total_impressions,
        ),
    )


def recommend(slots: TruthSlots, inventory_policy: TruthInventoryPolicy = TruthInventoryPolicy(), selection_policy: TruthSelectionPolicy = TruthSelectionPolicy(), root: Path = ROOT) -> tuple[TruthPlan, pd.DataFrame]:
    """Return the policy pick plus scored rows used to assert rejections/alternatives."""
    scored = scale(slots, inventory_policy, root)
    singles: list[TruthPlan] = []
    for _, row in scored.iterrows():
        if slots.budget >= selection_policy.min_line_spend and viable_alone(row, slots, inventory_policy):
            line = TruthLine(row.product_id, row.product_name, round(float(slots.budget), 2), int(row.affordable_imps), float(row.kpi_value), float(row.confidence))
            singles.append(TruthPlan("single", (line,), line.spend, 0.0, line.impressions, line.kpi_value, None if slots.impression_goal is None else line.impressions >= slots.impression_goal))
    bundle = best_bundle(slots, inventory_policy, selection_policy, root)
    if selection_policy.mode == "single_first" and singles:
        pick = max(singles, key=lambda p: (p.blended_kpi, p.total_impressions))
    else:
        pick = max([*singles, bundle], key=lambda p: ((p.blended_kpi if p.blended_kpi is not None else -1), -len(p.lines), p.total_impressions))
    return pick, scored


SAMPLE_SLOTS = {
    "Acme": TruthSlots("Acme Shoes", "Retail", "ctr", "US", 30_000),
    "Burger": TruthSlots("Burger Bazaar", "QSR", "in_view_rate", "US", 15_000, 800_000),
    "CineVerse": TruthSlots("CineVerse", "Entertainment", "ctr", "EMEA", 40_000),
    "Stellar": TruthSlots("Stellar Bank", "Finance", "ctr", "US", 20_000, 800_000),
    "Wanderlust": TruthSlots("Wanderlust Air", "Travel", "in_view_rate", "APAC", 18_000, 850_000),
}


def sample_expectations(root: Path = ROOT) -> dict[str, TruthPlan]:
    return {name: recommend(slots, root=root)[0] for name, slots in SAMPLE_SLOTS.items()}
