"""Typed contracts for the recommendation workflow.

The Recommendation is the structured response the UI renders (brief requirement 8.8) and the
artifact payload. Every number a strategist sees comes from here; `display` holds the same
numbers pre-formatted for the templated rationale.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

Vertical = Literal["Retail", "Finance", "Travel", "QSR", "Entertainment"]
Kpi = Literal["ctr", "in_view_rate"]
Geo = Literal["US", "EMEA", "APAC"]

VERTICALS: tuple[str, ...] = ("Retail", "Finance", "Travel", "QSR", "Entertainment")
KPIS: tuple[str, ...] = ("ctr", "in_view_rate")
GEOS: tuple[str, ...] = ("US", "EMEA", "APAC")
REQUIRED_SLOTS: tuple[str, ...] = ("vertical", "kpi", "geo", "budget")
KPI_LABELS = {"ctr": "CTR", "in_view_rate": "in-view rate"}


class Slots(BaseModel):
    """The details a recommendation needs (brief requirement 7). All optional until validated."""

    client_name: str | None = None
    vertical: str | None = None
    kpi: str | None = None
    geo: str | None = None
    budget: float | None = None
    impression_goal: int | None = None


class Benchmark(BaseModel):
    product_id: str
    product_name: str
    cpm: float
    line_items: int
    impressions: int
    clicks: int
    viewable_impressions: int
    ctr: float
    in_view_rate: float


class Candidate(BaseModel):
    product_id: str
    product_name: str
    cpm: float
    kpi_value: float
    kpi_rank: int  # 1 = best on the requested KPI
    affordable_imps: int  # floor(budget / cpm * 1000)


class ScaleCheck(BaseModel):
    product_id: str
    available_imps: int
    confidence: float  # the inventory_risk column: forecast confidence, higher = more reliable
    confidence_tier: Literal["high", "medium", "low"]
    capacity: int  # per InventoryPolicy
    absorbs_budget: bool
    deliverable_imps: int  # min(affordable, capacity)


class Rejection(BaseModel):
    code: Literal["INVENTORY_BELOW_BUDGET", "BUDGET_BELOW_GOAL", "INVENTORY_BELOW_GOAL", "BELOW_MIN_CONFIDENCE"]
    detail: dict[str, Any] = Field(default_factory=dict)


class PlanLine(BaseModel):
    product_id: str
    product_name: str
    spend: float
    impressions: int
    kpi_value: float
    confidence: float
    low_confidence: bool


class Plan(BaseModel):
    kind: Literal["single", "bundle", "none"]
    lines: list[PlanLine] = Field(default_factory=list)
    total_spend: float = 0.0
    unspent: float = 0.0
    total_impressions: int = 0
    blended_kpi: float = 0.0
    meets_goal: bool | None = None


class Alternative(BaseModel):
    product_ids: list[str]
    kind: Literal["rejected_leader", "runner_up", "bundle"]
    kpi_value: float
    reasons: list[str] = Field(default_factory=list)
    delta: dict[str, float] = Field(default_factory=dict)  # kpi_points, impression_shortfall, unabsorbed_spend


class Tradeoff(BaseModel):
    code: str
    facts: dict[str, Any] = Field(default_factory=dict)


class RejectedNote(BaseModel):
    """Brief requirement 9.6: one rejected top alternative and why it wasn't chosen."""

    product_id: str
    product_name: str
    explanation: str
    source: Literal["template", "llm"] = "template"


class Rationale(BaseModel):
    """Brief requirements 9.5 and 9.6. `text` joins best_fit and tradeoff for plain readers."""

    text: str
    source: Literal["template", "llm"] = "template"
    best_fit: str | None = None  # 9.5: why the selected product or bundle is the best fit
    tradeoff: str | None = None  # 8.7: what choosing it gives up
    rejected: list[RejectedNote] = Field(default_factory=list)  # 9.6: every product ranked above the pick


class Recommendation(BaseModel):
    requirements: Slots
    benchmarks: list[Benchmark]
    candidates: list[Candidate]
    scale: list[ScaleCheck]
    rejections: dict[str, list[Rejection]]
    plan: Plan
    alternatives: list[Alternative]
    tradeoffs: list[Tradeoff]
    display: dict[str, str]
    rationale: Rationale
    policy: dict[str, Any]
