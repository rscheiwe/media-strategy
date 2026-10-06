"""Summarise one or more ablation result folders into one table.

    python3 experiments/prompt_ablation/summarize.py results/<dirA> results/<dirB> ...

"fair" drops every brief whose vertical any variant's worked example reveals (briefs 1 and
3), for every variant, so all variants are compared on the same, unleaked cases.
"""

from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

LEAKED_ANY = {"brief-1", "brief-3"}
CRITERIA = ("numbers_correct", "kpi_reading_correct", "handles_missing_info", "addresses_viability", "conclusion_correct")


def passed(r: dict) -> bool:
    return all(v for v in r["grade"].values() if isinstance(v, bool))


def main(dirs: list[str]) -> None:
    runs = [r for d in dirs for r in json.loads((Path(d) / "runs.json").read_text())]
    variants = sorted({r["variant"] for r in runs})
    rate = lambda sel: f"{sum(passed(r) for r in sel)}/{len(sel)}"  # noqa: E731
    print(f"{'variant':8} {'all':>7} {'fair':>7} {'qsr':>5} {'scope':>6} {'briefs*':>8}  "
          + " ".join(f"{c.split('_')[0]:>9}" for c in CRITERIA) + f"  {'words':>5} {'secs':>5}")
    for v in variants:
        rs = [r for r in runs if r["variant"] == v]
        fair = [r for r in rs if r["case"] not in LEAKED_ANY]
        judged = [r for r in fair if not r["case"].startswith("scope")]
        crit = " ".join(f"{sum(bool(r['grade'].get(c)) for r in judged):>4}/{len(judged):<4}" for c in CRITERIA)
        print(f"{v:8} {rate(rs):>7} {rate(fair):>7} {rate([r for r in rs if r['case'] == 'qsr']):>5} "
              f"{rate([r for r in rs if r['case'].startswith('scope')]):>6} "
              f"{rate([r for r in fair if r['case'].startswith('brief')]):>8}  {crit}  "
              f"{statistics.median(r['words'] for r in rs):5.0f} {statistics.median(r['seconds'] for r in rs):5.1f}")
    print("\n* briefs excluding the leaked briefs 1 and 3; per-criterion columns are over fair judged cases.")


if __name__ == "__main__":
    main(sys.argv[1:])
