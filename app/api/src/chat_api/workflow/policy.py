"""Policies for the two judgement calls the brief leaves to us.

InventoryPolicy: how `inventory_risk` (forecast confidence, 0-1, higher = more reliable) enters
the scale check. The brief says "you must decide"; there is no industry formula. Ad servers tier
forecasts (e.g. Google Ad Manager's Available / Likely / Expected to deliver) and plan with
headroom. Default: discount available impressions by confidence (expected deliverable volume).

SelectionPolicy: single product first (the highest-KPI product viable alone), else the best
small bundle; or maximise blended KPI over singles and bundles.

Both are programmable (env `APP_INVENTORY_POLICY` / `APP_SELECTION_POLICY`, a mode name or a
JSON object of fields) and echoed into every Recommendation with a version hash.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import asdict, dataclass, fields
from typing import Literal


def _version(obj) -> str:
    return hashlib.sha256(json.dumps(asdict(obj), sort_keys=True).encode()).hexdigest()[:12]


@dataclass(frozen=True)
class InventoryPolicy:
    mode: Literal["adjusted", "raw", "threshold"] = "adjusted"
    min_confidence: float = 0.80  # threshold mode: below this, capacity is 0
    low_confidence: float = 0.80  # below this, a line carries a low-confidence warning
    headroom: float = 0.0  # keep this share of capacity unbooked, in every mode
    min_absorption: float = 1.0  # capacity must cover this share of the impressions bought

    def capacity(self, available: int, confidence: float) -> int:
        if self.mode == "raw":
            base = float(available)
        elif self.mode == "threshold":
            base = float(available) if confidence >= self.min_confidence else 0.0
        else:  # adjusted
            base = available * confidence
        return int(math.floor(base * (1 - self.headroom) + 1e-9))

    @staticmethod
    def tier(confidence: float) -> Literal["high", "medium", "low"]:
        if confidence >= 0.90:
            return "high"
        if confidence >= 0.80:
            return "medium"
        return "low"

    @property
    def version(self) -> str:
        return _version(self)

    def rule(self) -> str:
        """The usable-inventory rule in words ("forecast × confidence")."""
        if self.mode == "raw":
            text = "forecast available impressions"
        elif self.mode == "threshold":
            text = f"forecast if confidence ≥ {self.min_confidence:.2f}, else 0"
        else:
            text = "forecast × confidence"
        if self.headroom:
            text += f", minus {self.headroom:.0%} headroom"
        return text

    def describe(self) -> str:
        return f"Capacity = {self.rule()}"

    def pandas_expr(self, df: str = "b") -> str:
        """The same rule as a pandas expression over available_imps / inventory_risk columns, for
        the analysis recipes, so the analysis path computes usable inventory exactly as here."""
        if self.mode == "raw":
            expr = f"{df}.available_imps"
        elif self.mode == "threshold":
            expr = f"{df}.available_imps.where({df}.inventory_risk >= {self.min_confidence}, 0)"
        else:
            expr = f"{df}.available_imps * {df}.inventory_risk"
        if self.headroom:
            expr = f"({expr}) * {1 - self.headroom}"
        return f"({expr} + 1e-9).astype(int)"


@dataclass(frozen=True)
class SelectionPolicy:
    mode: Literal["single_first", "kpi_max"] = "single_first"
    max_lines: int = 3  # "small bundle": up to 3 products
    min_line_spend: float = 2000.0
    # Among bundles, prefer one that spends the budget (leftover under min_line_spend) before
    # the highest blended KPI: a 2-line bundle at a higher CTR that leaves a quarter of the
    # budget unplaced isn't the better plan.
    prefer_full_spend: bool = True
    bundle_uplift_to_surface: float = 0.05  # list the best bundle when it beats the pick by this

    @property
    def version(self) -> str:
        return _version(self)


def _from_env(cls, name: str):
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return cls()
    if raw.startswith("{"):
        data = json.loads(raw)
        allowed = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in allowed})
    return cls(mode=raw)


def inventory_policy() -> InventoryPolicy:
    return _from_env(InventoryPolicy, "APP_INVENTORY_POLICY")


def selection_policy() -> SelectionPolicy:
    return _from_env(SelectionPolicy, "APP_SELECTION_POLICY")


def policy_record(inventory: InventoryPolicy, selection: SelectionPolicy) -> dict:
    return {
        "inventory": asdict(inventory),
        "selection": asdict(selection),
        "version": hashlib.sha256((inventory.version + selection.version).encode()).hexdigest()[:12],
        "note": inventory.describe(),
    }
