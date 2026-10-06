"""Eval (f): policy sensitivity. Deterministic, no model calls.

    cd app/api && PYTHONPATH=. uv run python -m evals.run_policy

Every brief (the 5 complete ones, brief 6 for each vertical, brief 7 for each KPI) under each
inventory policy x selection policy. The brief gives no rule for inventory risk, so this is
the table that shows how much the pick depends on ours: a cell differs from the default
(adjusted + single_first) when the pick changes. Writes results/<ts>/policy/.
"""

from __future__ import annotations

import json
from typing import Any

from chat_api.casedata import connect_readonly, ensure_case_db
from chat_api.workflow.domain import VERTICALS, Slots
from chat_api.workflow.policy import InventoryPolicy, SelectionPolicy
from chat_api.workflow.steps import recommend

from .harness import results_dir

INVENTORY = {
    "raw": InventoryPolicy(mode="raw"),
    "adjusted (default)": InventoryPolicy(),
    "threshold 0.80": InventoryPolicy(mode="threshold", min_confidence=0.80),
    "adjusted + 10% headroom": InventoryPolicy(headroom=0.10),
    "adjusted, absorb 98%": InventoryPolicy(min_absorption=0.98),
}
SELECTION = {"single_first (default)": SelectionPolicy(), "kpi_max": SelectionPolicy(mode="kpi_max")}
DEFAULT = ("adjusted (default)", "single_first (default)")

BRIEFS: dict[str, dict[str, Any]] = {
    "Acme": {"vertical": "Retail", "kpi": "ctr", "geo": "US", "budget": 30000},
    "Burger": {"vertical": "QSR", "kpi": "in_view_rate", "geo": "US", "budget": 15000, "impression_goal": 800000},
    "CineVerse": {"vertical": "Entertainment", "kpi": "ctr", "geo": "EMEA", "budget": 40000},
    "Stellar": {"vertical": "Finance", "kpi": "ctr", "geo": "US", "budget": 20000, "impression_goal": 800000},
    "Wanderlust": {"vertical": "Travel", "kpi": "in_view_rate", "geo": "APAC", "budget": 18000, "impression_goal": 850000},
    **{f"Brief6/{v}": {"vertical": v, "kpi": "in_view_rate", "geo": "US", "budget": 25000} for v in VERTICALS},
    "Brief7/CTR": {"vertical": "Finance", "kpi": "ctr", "geo": "EMEA", "budget": 25000},
    "Brief7/in-view": {"vertical": "Finance", "kpi": "in_view_rate", "geo": "EMEA", "budget": 25000},
}


def pick(rec) -> str:
    if rec.plan.kind == "none":
        return "no viable plan"
    names = " + ".join(line.product_name for line in rec.plan.lines)
    return names if rec.plan.kind == "single" else f"{names} (bundle)"


def main() -> None:
    conn = connect_readonly(ensure_case_db())
    cells: dict[str, dict[str, str]] = {}
    for name, slots in BRIEFS.items():
        for inv_name, inv in INVENTORY.items():
            for sel_name, sel in SELECTION.items():
                rec = recommend(conn, Slots(**slots), inventory_policy=inv, selection_policy=sel)
                cells.setdefault(name, {})[f"{inv_name} | {sel_name}"] = pick(rec)
    conn.close()

    columns = [f"{i} | {s}" for i in INVENTORY for s in SELECTION]
    default = f"{DEFAULT[0]} | {DEFAULT[1]}"
    changed = {b: [c for c in columns if row[c] != row[default]] for b, row in cells.items()}
    out = results_dir("policy")
    (out / "cells.json").write_text(json.dumps({"cells": cells, "changed": changed}, indent=2))

    lines = ["# Policy sensitivity (deterministic)", "",
             f"Default policy: **{default}**. A **bold** cell is a pick that differs from the default.", ""]
    for sel_name in SELECTION:
        lines += [f"## Selection: {sel_name}", "", "| brief | " + " | ".join(INVENTORY) + " |", "|---|" + "---|" * len(INVENTORY)]
        for b, row in cells.items():
            vals = []
            for inv_name in INVENTORY:
                v = row[f"{inv_name} | {sel_name}"]
                vals.append(v if v == row[default] else f"**{v}**")
            lines.append(f"| {b} | " + " | ".join(vals) + " |")
        lines.append("")
    moved = {b: c for b, c in changed.items() if c}
    lines += ["## Briefs whose pick depends on the policy", ""] + [f"- **{b}**: {len(c)}/{len(columns) - 1} alternative policies change it" for b, c in moved.items()]
    (out / "report.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\n-> {out}")


if __name__ == "__main__":
    main()
