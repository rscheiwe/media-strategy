"""Acme Shoes (Retail, CTR, US, $30,000), solved step by step. Read alongside docs/OPTIMIZATION_SOLVERS.md.

    cd experiments/solvers && uv run python acme.py

1. The inputs: rate, CPM, outcomes per dollar, usable inventory.
2. A two-product LP you can see: the feasible region's corners, and the optimum at one of them.
3. Shadow prices (the LP dual): what one more dollar, or one more unit of inventory, is worth.
4. All six products: brute force vs MILP (branch and bound) vs CP-SAT, maximizing clicks.
5. Maximizing the blended rate instead, with Dinkelbach's method.
6. The app's own answers, for comparison.
7. Bonus for SAT learners: a CNF formula solved as a 0-1 integer program.
"""

from __future__ import annotations

import itertools

import numpy as np
from scipy.optimize import linprog, milp, LinearConstraint, Bounds

from model import (
    MIN_LINE, app_bundle, app_pick, brute_force, load, max_blended, outcome_weights, solve_cpsat, solve_milp,
)


def heading(text: str) -> None:
    print(f"\n{'=' * 78}\n{text}\n{'=' * 78}")


def polygon_vertices(A: np.ndarray, b: np.ndarray) -> list[tuple[float, float]]:
    """Corners of {x in R^2 : A x <= b}: intersect every pair of constraint lines, keep feasible points."""
    pts = []
    for i, j in itertools.combinations(range(len(A)), 2):
        M = A[[i, j]]
        if abs(np.linalg.det(M)) < 1e-12:
            continue  # parallel lines
        x = np.linalg.solve(M, b[[i, j]])
        if np.all(A @ x <= b + 1e-6) and not any(np.allclose(x, p) for p in pts):
            pts.append(x)
    center = np.mean(pts, axis=0)
    pts.sort(key=lambda p: np.arctan2(p[1] - center[1], p[0] - center[0]))
    return [tuple(p) for p in pts]


def main() -> None:
    brief = load("Retail", "ctr", "US", 30000)
    by_name = {p.name: p for p in brief.products}

    heading("1. Inputs (from the app's own workflow steps)")
    print(f"{'Product':<20}{'CTR':>8}{'CPM':>7}{'clicks/$':>10}{'usable $':>11}")
    for p in sorted(brief.products, key=lambda p: -p.rate):
        print(f"{p.name:<20}{p.rate:>8.3%}{p.cpm:>7.0f}{p.per_dollar:>10.3f}{p.max_spend:>11,.0f}")
    print("\nRanked by CTR (the app's order) vs by clicks per dollar (what maximizes clicks): different orders.")

    heading("2. A two-product LP you can see: Commerce Connect (x) and Attention Builder (y)")
    cc, ab = by_name["Commerce Connect"], by_name["Attention Builder"]
    w = np.array([cc.per_dollar, ab.per_dollar])
    A = np.array([[1, 1], [1, 0], [0, 1], [-1, 0], [0, -1]], dtype=float)
    b = np.array([brief.budget, cc.max_spend, ab.max_spend, 0, 0])
    print(f"maximize {w[0]:.3f}*x + {w[1]:.3f}*y   subject to  x + y <= {brief.budget:,.0f},  "
          f"x <= {cc.max_spend:,.0f},  y <= {ab.max_spend:,.0f},  x, y >= 0")
    print("\nThe feasible region is a polygon. A linear objective is maximized at a corner:")
    corners = polygon_vertices(A, b)
    best = max(corners, key=lambda v: w @ v)
    for v in corners:
        mark = "  <- optimum" if v == best else ""
        v = (abs(v[0]) + 0.0, abs(v[1]) + 0.0) if min(v) > -1e-6 else v
        print(f"  corner (x={v[0]:>9,.0f}, y={v[1]:>9,.0f})  clicks {w @ np.array(v):>9,.0f}{mark}")
    res = linprog(-w, A_ub=A[:3], b_ub=b[:3], bounds=[(0, None)] * 2, method="highs")
    print(f"\nlinprog (HiGHS) agrees: x={res.x[0]:,.0f}, y={res.x[1]:,.0f}, clicks {-res.fun:,.0f}")
    print("Same as rank-order filling for these two: fill Commerce Connect to its limit, the rest to Attention Builder.")

    heading("3. Shadow prices: the LP dual")
    # linprog minimizes -clicks, so a constraint's shadow price (clicks per unit) is -marginal.
    names = ["budget (per $1)", "Commerce Connect inventory (per $1)", "Attention Builder inventory (per $1)"]
    for name, m in zip(names, res.ineqlin.marginals):
        print(f"  {name:<38} {-m:.3f} clicks")
    print("One more budget dollar goes to Attention Builder (0.711 clicks). Loosening Commerce Connect's inventory\n"
          "by a dollar is worth its edge over Attention Builder (0.817 - 0.711). A non-binding constraint is worth 0.")

    heading("4. All six products, maximizing clicks: three exact methods")
    weights = outcome_weights(brief)
    plans = [brute_force(brief, weights), solve_milp(brief, weights), solve_cpsat(brief)]
    for plan in plans:
        print(plan.describe(brief))
        print(f"{'':<24} {plan.info}")
    print("\nBrute force solves an LP for each of the 41 subsets of 1-3 products. The MILP solves ONE model by\n"
          "branch and bound; a gap of 0 means its dual bound equals the plan found, i.e. proven optimal. CP-SAT\n"
          "uses whole dollars and rounded weights, so it can differ in the last digits.")

    heading("5. Maximizing the blended CTR instead (spend first), with Dinkelbach's method")
    for solve, name in ((solve_milp, "blended, MILP"), (brute_force, "blended, brute force")):
        plan = max_blended(brief, solve, name)
        print(plan.describe(brief))
        print(f"{'':<24} {plan.info.get('dinkelbach_iterations')} Dinkelbach iterations")
    print("\nThe highest blended rate spends the leftover on Premium Takeover: expensive, so few impressions to\n"
          "dilute the average. That's the 'pricey, low-volume line' the app's rules deliberately avoid.")

    heading("6. The app's answers")
    for plan in (app_pick(brief), app_bundle(brief)):
        print(plan.describe(brief))
    print("\nThe app picks Premium Takeover alone (single-first). Its bundle, the rank-order fill, sits between the\n"
          "two optima above: close to the most clicks, at a higher rate. A heuristic, not the optimum of one objective.")

    heading("7. Bonus: SAT as a 0-1 integer program")
    # (x1 or not x2) and (x2 or x3) and (not x1 or not x3) and (not x3 or x2)
    cnf = [[1, -2], [2, 3], [-1, -3], [-3, 2]]
    n = 3
    rows, lb = [], []
    for clause in cnf:  # a clause becomes: sum(x for positive literals) + sum(1 - x for negative) >= 1
        row = np.zeros(n)
        constant = 0
        for lit in clause:
            if lit > 0:
                row[lit - 1] += 1
            else:
                row[-lit - 1] -= 1
                constant += 1
        rows.append(row)
        lb.append(1 - constant)
    res = milp(c=np.zeros(n), constraints=LinearConstraint(np.array(rows), lb, np.inf),
               integrality=np.ones(n), bounds=Bounds(0, 1))
    print("CNF: (x1 or not x2) and (x2 or x3) and (not x1 or not x3) and (not x3 or x2)")
    if res.status == 0:
        x = np.round(res.x).astype(int)
        print(f"MILP finds a satisfying assignment: x1={x[0]}, x2={x[1]}, x3={x[2]}")
        ok = all(any((x[abs(l) - 1] == 1) if l > 0 else (x[abs(l) - 1] == 0) for l in c) for c in cnf)
        print(f"Checked against the clauses: {'satisfied' if ok else 'NOT satisfied'}")
    else:
        print("MILP proves it unsatisfiable (infeasible).")
    print(f"\n(The line minimum is ${MIN_LINE:,.0f} throughout; at most 3 products per plan.)")


if __name__ == "__main__":
    main()
