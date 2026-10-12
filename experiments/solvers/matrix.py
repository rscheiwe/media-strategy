"""All 420 briefs: do the solvers agree with brute force, and how does the app's rule compare?

    cd experiments/solvers && uv run python matrix.py [--limit N]

The same grid as app/api/evals/run_matrix.py: 5 verticals x 2 KPIs x 3 geos x 7 budgets x
(goal or none). For each brief:

1. Correctness (exact methods must agree):
   - maximize outcomes: brute force (an LP per subset) vs MILP (HiGHS) vs CP-SAT (whole dollars);
   - maximize blended rate after maximizing spend (Dinkelbach): brute force vs MILP;
   - feasibility: the same briefs infeasible everywhere.
2. The app's rule vs the two optima: how many outcomes and how much blended rate rank-order
   filling and single-first give up, and whether the app ever says "no plan" when one exists.

Writes results/<timestamp>/matrix.json (gitignored); the summary is printed.
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
from datetime import datetime
from pathlib import Path

from model import (
    all_briefs, app_bundle, app_pick, brute_force, max_blended, outcome_weights, solve_cpsat, solve_milp,
)


def close(a: float, b: float, rel: float = 1e-6, abs_: float = 1e-6) -> bool:
    return abs(a - b) <= max(abs_, rel * max(abs(a), abs(b)))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="only the first N briefs (smoke test)")
    args = ap.parse_args()

    timings = {"brute force": 0.0, "MILP": 0.0, "CP-SAT": 0.0, "blended brute": 0.0, "blended MILP": 0.0}
    rows, problems = [], []
    for k, brief in enumerate(all_briefs()):
        if args.limit and k >= args.limit:
            break
        w = outcome_weights(brief)
        t = time.perf_counter(); bf = brute_force(brief, w); timings["brute force"] += time.perf_counter() - t
        t = time.perf_counter(); mi = solve_milp(brief, w); timings["MILP"] += time.perf_counter() - t
        t = time.perf_counter(); cp = solve_cpsat(brief); timings["CP-SAT"] += time.perf_counter() - t
        t = time.perf_counter(); bb = max_blended(brief, brute_force, "blended brute"); timings["blended brute"] += time.perf_counter() - t
        t = time.perf_counter(); bm = max_blended(brief, solve_milp, "blended MILP"); timings["blended MILP"] += time.perf_counter() - t
        pick, bundle = app_pick(brief), app_bundle(brief)

        feas = {"brute": bf.feasible, "milp": mi.feasible, "cpsat": cp.feasible, "blended brute": bb.feasible, "blended milp": bm.feasible}
        if len(set(feas.values())) > 1:
            problems.append((brief.label, "feasibility differs", feas))
        row = {"brief": brief.label, "feasible": mi.feasible, "app_pick": pick.feasible, "app_bundle": bundle.feasible}
        if mi.feasible and bf.feasible:
            if not close(bf.outcomes(brief), mi.outcomes(brief)):
                problems.append((brief.label, "max outcomes: brute vs MILP", bf.outcomes(brief), mi.outcomes(brief)))
            if cp.feasible and not close(cp.outcomes(brief), mi.outcomes(brief), rel=1e-4, abs_=2.0):
                problems.append((brief.label, "max outcomes: CP-SAT vs MILP", cp.outcomes(brief), mi.outcomes(brief)))
            if mi.info.get("gap") not in (None, 0.0) and mi.info["gap"] > 1e-6:
                problems.append((brief.label, "MILP gap not closed", mi.info))
        if bm.feasible and bb.feasible:
            if not close(bb.blended(brief), bm.blended(brief), rel=1e-7):
                problems.append((brief.label, "max blended: brute vs MILP", bb.blended(brief), bm.blended(brief)))
        if mi.feasible:
            max_out = mi.outcomes(brief)
            row.update(max_outcomes=max_out, max_blended=bm.blended(brief) if bm.feasible else None,
                       nodes=mi.info.get("nodes"), dinkelbach=bm.info.get("dinkelbach_iterations"))
            if pick.feasible:
                row.update(pick_outcomes=pick.outcomes(brief), pick_blended=pick.blended(brief),
                           pick_lines=len(pick.lines(brief)))
            if bundle.feasible:
                row.update(bundle_outcomes=bundle.outcomes(brief), bundle_blended=bundle.blended(brief))
        rows.append(row)
        if (k + 1) % 60 == 0:
            print(f"  {k + 1} briefs...")

    n = len(rows)
    feasible = [r for r in rows if r["feasible"]]
    print(f"\n=== Correctness ({n} briefs) ===")
    print(f"disagreements between exact methods: {len(problems)}")
    for p in problems[:10]:
        print("  ", p)
    print(f"feasible: {len(feasible)}, infeasible: {n - len(feasible)}")
    app_none_but_feasible = [r["brief"] for r in feasible if not r["app_pick"]]
    print(f"app says 'no plan' but a plan exists: {len(app_none_but_feasible)}")
    for b in app_none_but_feasible[:10]:
        print("   ", b)
    nodes = [r["nodes"] for r in feasible if r.get("nodes") is not None]
    if nodes:
        print(f"MILP branch-and-bound nodes: median {statistics.median(nodes)}, max {max(nodes)}")
    dk = [r["dinkelbach"] for r in feasible if r.get("dinkelbach")]
    if dk:
        print(f"Dinkelbach iterations: median {statistics.median(dk)}, max {max(dk)}")

    print("\n=== The app's rule vs the optima (feasible briefs) ===")
    with_pick = [r for r in feasible if "pick_outcomes" in r]
    share = [r["pick_outcomes"] / r["max_outcomes"] for r in with_pick]
    print(f"app pick (single-first): outcomes as a share of the maximum: median {statistics.median(share):.1%}, "
          f"min {min(share):.1%}; at >= 99.9% in {sum(s >= 0.999 for s in share)}/{len(share)}")
    singles = [r for r in with_pick if r["pick_lines"] == 1]
    s_share = [r["pick_outcomes"] / r["max_outcomes"] for r in singles]
    if s_share:
        print(f"  of which single-product picks ({len(singles)}): median {statistics.median(s_share):.1%}, min {min(s_share):.1%}")
    with_bundle = [r for r in feasible if "bundle_outcomes" in r]
    b_share = [r["bundle_outcomes"] / r["max_outcomes"] for r in with_bundle]
    if b_share:
        print(f"app bundle (rank fill): outcomes as a share of the maximum: median {statistics.median(b_share):.1%}, "
              f"min {min(b_share):.1%}; at >= 99.9% in {sum(s >= 0.999 for s in b_share)}/{len(b_share)}")
        gap = [r["max_blended"] - r["bundle_blended"] for r in with_bundle if r.get("max_blended")]
        print(f"app bundle blended rate vs the maximum (spend first): median {statistics.median(gap) * 100:.3f} points below, "
              f"max {max(gap) * 100:.3f}")

    print("\n=== Time (all briefs) ===")
    for k, v in timings.items():
        print(f"  {k:<14} {v:6.2f}s")

    out = Path(__file__).parent / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True, exist_ok=True)
    (out / "matrix.json").write_text(json.dumps({"rows": rows, "problems": [list(map(str, p)) for p in problems],
                                                  "timings": timings}, indent=1))
    print(f"\n-> {out / 'matrix.json'}")


if __name__ == "__main__":
    main()
