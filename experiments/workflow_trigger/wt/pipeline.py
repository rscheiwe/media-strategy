"""The recommendation workflow, on the real case-study data.

Deterministic stages (benchmarks, scale, viability, allocate) run behind the
upstream service; the "analyst" stage there optionally adds an LLM summary.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

DATA_DIR = Path(__file__).resolve().parents[3] / "app" / "api" / "case_data"

VERTICALS = ("Retail", "Finance", "Travel", "QSR", "Entertainment")
KPIS = ("ctr", "in_view_rate")
GEOS = ("US", "EMEA", "APAC")
REQUIRED_SLOTS = ("vertical", "kpi", "geo", "budget")
LOW_CONFIDENCE = 0.80
MAX_LINES = 3
MIN_LINE_SPEND = 2000.0


@dataclass(frozen=True)
class Data:
    catalog: dict[str, dict[str, Any]]
    history: list[dict[str, Any]]
    inventory: dict[tuple[str, str, str], tuple[int, float]]
    briefs: list[str]


@lru_cache(maxsize=1)
def load_data(data_dir: Path = DATA_DIR) -> Data:
    """Read the four case-study files once."""
    with open(data_dir / "product_catalog.csv") as f:
        catalog = {
            r["product_id"]: {"name": r["product_name"], "cpm": float(r["cpm"])}
            for r in csv.DictReader(f)
        }
    with open(data_dir / "campaign_history.csv") as f:
        history = [
            {
                "product_id": r["product_id"],
                "vertical": r["vertical"],
                "impressions": int(r["impressions"]),
                "clicks": int(r["clicks"]),
                "viewable": int(r["viewable_impressions"]),
            }
            for r in csv.DictReader(f)
        ]
    with open(data_dir / "inventory_forecaster.csv") as f:
        inventory = {
            (r["product_id"], r["vertical"], r["geo"]): (
                int(r["available_imps"]),
                float(r["inventory_risk"]),
            )
            for r in csv.DictReader(f)
        }
    briefs = json.loads((data_dir / "client_briefs.json").read_text())
    return Data(catalog, history, inventory, briefs)


def missing_slots(slots: dict[str, Any]) -> list[str]:
    """Required slots that are absent or invalid."""
    return [name for name in REQUIRED_SLOTS if not _valid(name, slots.get(name))]


def _valid(name: str, value: Any) -> bool:
    if name == "budget":
        return isinstance(value, (int, float)) and value > 0
    options = {"vertical": VERTICALS, "kpi": KPIS, "geo": GEOS}[name]
    return value in options


def benchmarks(slots: dict[str, Any], data: Data) -> dict[str, dict[str, float]]:
    """Impression-weighted CTR and in-view rate per product for the vertical."""
    totals: dict[str, list[int]] = {}
    for row in data.history:
        if row["vertical"] != slots["vertical"]:
            continue
        t = totals.setdefault(row["product_id"], [0, 0, 0, 0])
        t[0] += row["impressions"]
        t[1] += row["clicks"]
        t[2] += row["viewable"]
        t[3] += 1
    return {
        pid: {"ctr": c / i, "in_view_rate": v / i, "line_items": n}
        for pid, (i, c, v, n) in totals.items()
    }


def scale(slots: dict[str, Any], bench: dict[str, Any], data: Data) -> dict[str, dict[str, Any]]:
    """Impressions the budget buys vs risk-adjusted inventory, per product."""
    out = {}
    for pid in bench:
        available, risk = data.inventory.get((pid, slots["vertical"], slots["geo"]), (0, 0.0))
        cpm = data.catalog[pid]["cpm"]
        out[pid] = {
            "cpm": cpm,
            "affordable_imps": int(slots["budget"] / cpm * 1000),
            "available_imps": available,
            "inventory_risk": risk,
            "effective_capacity": int(available * risk),
        }
    return out


def viability(slots: dict[str, Any], scaled: dict[str, Any]) -> dict[str, str]:
    """Products that can't meet the impression goal, with the reason."""
    goal = slots.get("impression_goal") or 0
    rejected = {}
    for pid, s in scaled.items():
        if s["effective_capacity"] < goal:
            rejected[pid] = "INVENTORY_BELOW_GOAL"
        elif s["affordable_imps"] < goal:
            rejected[pid] = "BUDGET_BELOW_GOAL"
    return rejected


def allocate(
    slots: dict[str, Any], bench: dict[str, Any], scaled: dict[str, Any], rejected: dict[str, str],
    data: Data,
) -> dict[str, Any]:
    """Greedy bundle: best product by KPI up to its effective capacity, then the next."""
    kpi = slots["kpi"]
    remaining = float(slots["budget"])
    ranked = sorted((p for p in bench if p not in rejected), key=lambda p: bench[p][kpi], reverse=True)
    lines = []
    for pid in ranked:
        if remaining < MIN_LINE_SPEND or len(lines) == MAX_LINES:
            break
        s = scaled[pid]
        spend = min(remaining, s["effective_capacity"] * s["cpm"] / 1000)
        if spend < MIN_LINE_SPEND:
            continue
        lines.append(
            {
                "product_id": pid,
                "product_name": data.catalog[pid]["name"],
                "spend": round(spend, 2),
                "impressions": int(spend / s["cpm"] * 1000),
                "kpi_benchmark": round(bench[pid][kpi], 5),
                "low_confidence": s["inventory_risk"] < LOW_CONFIDENCE,
            }
        )
        remaining -= spend
    total_imps = sum(line["impressions"] for line in lines)
    blended = (
        sum(line["impressions"] * line["kpi_benchmark"] for line in lines) / total_imps
        if total_imps
        else 0.0
    )
    goal = slots.get("impression_goal")
    return {
        "lines": lines,
        "unspent": round(max(remaining, 0.0), 2),
        "total_impressions": total_imps,
        "blended_kpi": round(blended, 5),
        "meets_goal": None if not goal else total_imps >= goal,
        "top_kpi_product": ranked[0] if ranked else None,
    }
