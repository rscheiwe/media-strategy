"""The bundle problem, four ways: brute force, MILP (HiGHS), CP-SAT, and the app's own rule.

Shared by `acme.py` (one brief, explained step by step) and `matrix.py` (all 420 briefs).
See docs/OPTIMIZATION_SOLVERS.md for the concepts.

The inputs come from the app itself (`chat_api.workflow.steps`), so every solver sees the same
benchmarks and usable inventory (forecast x confidence) as the recommendation card.

Decision variables, per product i:
    s_i >= 0        spend in dollars (continuous for MILP, whole dollars for CP-SAT)
    y_i in {0, 1}   whether product i is in the plan

Constraints (the app's rules, docs/WORKFLOW.md section 3.6):
    sum s_i <= B                         budget
    s_i <= M_i * y_i                     usable inventory, M_i = capacity * CPM / 1000 dollars
    s_i >= 2000 * y_i                    the $2,000 line minimum
    sum y_i <= 3                         at most three products
    sum s_i * 1000 / CPM_i >= goal       the impression goal, if any

Objectives:
    "outcomes"  maximize expected clicks (CTR) or viewable impressions (in-view): sum s_i * w_i,
                with w_i = rate_i * 1000 / CPM_i, the outcomes per dollar. Linear.
    "blended"   lexicographic: first maximize spend, then maximize the blended rate
                (outcomes / impressions) among plans that spend it. The ratio isn't linear, so
                it's solved with Dinkelbach's method: a short sequence of linear problems.
"""

from __future__ import annotations

import itertools
import math
import os
import sys
import tempfile
from dataclasses import dataclass, field
from functools import reduce
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, linprog, milp

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "app" / "api" / "src"))
os.environ.setdefault("APP_CASE_DATA_DIR", str(ROOT / "app" / "api" / "case_data"))
os.environ.setdefault("APP_CASE_DB_PATH", str(Path(tempfile.mkdtemp(prefix="solvers-")) / "case.db"))

from chat_api.casedata import connect_readonly, ensure_case_db  # noqa: E402
from chat_api.workflow import steps  # noqa: E402
from chat_api.workflow.domain import Slots  # noqa: E402
from chat_api.workflow.policy import InventoryPolicy, SelectionPolicy  # noqa: E402

MIN_LINE = 2000.0
MAX_LINES = 3
VERTICALS = ("Retail", "Finance", "Travel", "QSR", "Entertainment")
KPIS = ("ctr", "in_view_rate")
GEOS = ("US", "EMEA", "APAC")
BUDGETS = (5000, 10000, 15000, 25000, 40000, 60000, 100000)
GOALS = (None, 800000)

_conn = None


def conn():
    global _conn
    if _conn is None:
        _conn = connect_readonly(ensure_case_db(rebuild=True))
    return _conn


# --- the problem ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    cpm: float
    rate: float  # the KPI rate (CTR or in-view rate)
    max_spend: float  # usable inventory in dollars: capacity * CPM / 1000

    @property
    def per_dollar(self) -> float:
        """Expected outcomes per dollar: rate x 1000 / CPM."""
        return self.rate * 1000 / self.cpm

    @property
    def imps_per_dollar(self) -> float:
        return 1000 / self.cpm


@dataclass(frozen=True)
class Brief:
    vertical: str
    kpi: str
    geo: str
    budget: float
    goal: int | None
    products: tuple[Product, ...]

    @property
    def label(self) -> str:
        goal = f", goal {self.goal:,}" if self.goal else ""
        return f"{self.vertical}/{self.kpi}/{self.geo} ${self.budget:,.0f}{goal}"


def load(vertical: str, kpi: str, geo: str, budget: float, goal: int | None = None) -> Brief:
    """Benchmarks, CPMs and usable inventory, computed by the app's own workflow steps."""
    slots = Slots(vertical=vertical, kpi=kpi, geo=geo, budget=budget, impression_goal=goal)
    bench = steps.compute_benchmarks(conn(), vertical)
    cands = steps.select_products(conn(), slots, bench)
    scale = {s.product_id: s for s in steps.check_scale(conn(), slots, cands, InventoryPolicy())}
    products = tuple(
        Product(c.product_id, c.product_name, c.cpm, c.kpi_value, scale[c.product_id].capacity * c.cpm / 1000)
        for c in cands
    )
    return Brief(vertical, kpi, geo, float(budget), goal, products)


def all_briefs():
    for v, k, g, b, goal in itertools.product(VERTICALS, KPIS, GEOS, BUDGETS, GOALS):
        yield load(v, k, g, b, goal)


# --- a plan and its numbers ------------------------------------------------------------------


@dataclass
class Plan:
    method: str
    spend: dict[str, float] = field(default_factory=dict)  # product id -> dollars
    feasible: bool = True
    info: dict = field(default_factory=dict)  # solver details: gap, nodes, bound, iterations

    def lines(self, brief: Brief) -> list[tuple[Product, float]]:
        by_id = {p.id: p for p in brief.products}
        return [(by_id[i], s) for i, s in self.spend.items() if s > 1e-6]

    def total_spend(self, brief: Brief) -> float:
        return sum(s for _, s in self.lines(brief))

    def impressions(self, brief: Brief) -> float:
        return sum(s * p.imps_per_dollar for p, s in self.lines(brief))

    def outcomes(self, brief: Brief) -> float:
        return sum(s * p.per_dollar for p, s in self.lines(brief))

    def blended(self, brief: Brief) -> float:
        imps = self.impressions(brief)
        return self.outcomes(brief) / imps if imps else 0.0

    def describe(self, brief: Brief) -> str:
        if not self.feasible:
            return f"{self.method:<24} infeasible"
        parts = " + ".join(f"{p.name} ${s:,.0f}" for p, s in sorted(self.lines(brief), key=lambda t: -t[1]))
        return (
            f"{self.method:<24} {parts}\n{'':<24} spend ${self.total_spend(brief):,.0f}, "
            f"{self.outcomes(brief):,.0f} outcomes, blended {self.blended(brief):.3%}"
        )


# --- 1. brute force: try every subset, solve a small LP for each ---------------------------------


def _subset_lp(brief: Brief, subset: tuple[int, ...], weights: np.ndarray, min_spend: float = 0.0):
    """For a FIXED set of products, the best split is a plain LP (no integer variables).

    Maximize sum(weights_i * s_i) over s_i in [2000, max_spend_i], within the budget, meeting
    the goal, and spending at least `min_spend`. Returns scipy's result (or None if infeasible).
    """
    P = [brief.products[i] for i in subset]
    if any(p.max_spend < MIN_LINE for p in P):
        return None
    A_ub = [np.ones(len(P))]  # budget
    b_ub = [brief.budget]
    if brief.goal:
        A_ub.append(-np.array([p.imps_per_dollar for p in P]))  # goal, as -imps <= -goal
        b_ub.append(-brief.goal)
    if min_spend:
        A_ub.append(-np.ones(len(P)))  # spend at least min_spend
        b_ub.append(-min_spend)
    res = linprog(
        -weights[list(subset)], A_ub=np.array(A_ub), b_ub=b_ub,
        bounds=[(MIN_LINE, p.max_spend) for p in P], method="highs",
    )
    return res if res.status == 0 else None


def brute_force(brief: Brief, weights: np.ndarray, min_spend: float = 0.0, method: str = "brute force") -> Plan:
    """Exhaustive search: every subset of 1-3 products, each solved exactly as an LP.

    This is the oracle. It's exact, and fine at 6 products (41 subsets), but the number of
    subsets grows like n^3: about 20.8 million at 500 products.
    """
    best, best_val = None, -math.inf
    n = len(brief.products)
    for size in range(1, MAX_LINES + 1):
        for subset in itertools.combinations(range(n), size):
            res = _subset_lp(brief, subset, weights, min_spend)
            if res is not None and -res.fun > best_val + 1e-9:
                best_val = -res.fun
                best = {brief.products[i].id: float(s) for i, s in zip(subset, res.x)}
    if best is None:
        return Plan(method, feasible=False)
    return Plan(method, best, info={"subsets": sum(math.comb(n, k) for k in range(1, MAX_LINES + 1))})


# --- 2. MILP: one model, solved by branch and bound (HiGHS) ---------------------------------------


def solve_milp(brief: Brief, weights: np.ndarray, min_spend: float = 0.0, method: str = "MILP (HiGHS)") -> Plan:
    """The whole problem as one mixed-integer linear program.

    Variables x = [s_1..s_n, y_1..y_n]. scipy's milp minimizes, so the objective is negated.
    """
    P = brief.products
    n = len(P)
    M = np.array([p.max_spend for p in P])
    rows, lb, ub = [], [], []

    def add(row, lo, hi):
        rows.append(row); lb.append(lo); ub.append(hi)

    add(np.r_[np.ones(n), np.zeros(n)], -np.inf, brief.budget)  # sum s <= B
    for i in range(n):
        e = np.zeros(2 * n)
        e[i], e[n + i] = 1.0, -M[i]
        add(e, -np.inf, 0.0)  # s_i - M_i y_i <= 0      (inventory, and s_i = 0 when y_i = 0)
        e = np.zeros(2 * n)
        e[i], e[n + i] = 1.0, -MIN_LINE
        add(e, 0.0, np.inf)  # s_i - 2000 y_i >= 0     (line minimum when y_i = 1)
    add(np.r_[np.zeros(n), np.ones(n)], -np.inf, MAX_LINES)  # sum y <= 3
    if brief.goal:
        add(np.r_[[p.imps_per_dollar for p in P], np.zeros(n)], brief.goal, np.inf)  # impressions >= goal
    if min_spend:
        add(np.r_[np.ones(n), np.zeros(n)], min_spend, np.inf)

    res = milp(
        c=-np.r_[weights, np.zeros(n)],
        constraints=LinearConstraint(np.array(rows), lb, ub),
        integrality=np.r_[np.zeros(n), np.ones(n)],
        bounds=Bounds(np.zeros(2 * n), np.r_[np.full(n, brief.budget), np.ones(n)]),
    )
    if res.status != 0:
        return Plan(method, feasible=False, info={"status": res.message})
    info = {
        "objective": -res.fun,
        "dual_bound": -getattr(res, "mip_dual_bound", res.fun),
        "gap": getattr(res, "mip_gap", None),
        "nodes": getattr(res, "mip_node_count", None),
    }
    return Plan(method, {P[i].id: float(res.x[i]) for i in range(n)}, info=info)


# --- 3. CP-SAT: a CDCL SAT engine doing integer optimization (OR-Tools) ---------------------------


def solve_cpsat(brief: Brief, scale: int = 1_000_000, method: str = "CP-SAT (OR-Tools)") -> Plan:
    """Maximize outcomes with CP-SAT. Everything must be an integer, so:

    - spend is in whole dollars;
    - outcomes per dollar are scaled by `scale` and rounded to integer weights;
    - the goal, sum s_i * 1000 / CPM_i >= goal, is multiplied through by L = lcm(CPMs), which
      makes every coefficient an integer (the case data's CPMs are whole dollars).
    """
    from ortools.sat.python import cp_model

    P = brief.products
    assert all(float(p.cpm).is_integer() for p in P), "CP-SAT needs integer CPMs for the goal constraint"
    m = cp_model.CpModel()
    B = int(brief.budget)
    s = [m.NewIntVar(0, B, f"s_{p.id}") for p in P]
    y = [m.NewBoolVar(f"y_{p.id}") for p in P]
    for si, yi, p in zip(s, y, P):
        m.Add(si <= int(math.floor(p.max_spend)) * yi)
        m.Add(si >= int(MIN_LINE) * yi)
    m.Add(sum(s) <= B)
    m.Add(sum(y) <= MAX_LINES)
    if brief.goal:
        L = reduce(math.lcm, (int(p.cpm) for p in P))
        m.Add(sum(si * (1000 * L // int(p.cpm)) for si, p in zip(s, P)) >= brief.goal * L)
    m.Maximize(sum(si * round(p.per_dollar * scale) for si, p in zip(s, P)))
    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = 10
    status = solver.Solve(m)
    if status not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return Plan(method, feasible=False, info={"status": solver.StatusName(status)})
    info = {
        "status": solver.StatusName(status),
        "objective": solver.ObjectiveValue() / scale,
        "bound": solver.BestObjectiveBound() / scale,
        "conflicts": solver.NumConflicts(),
        "branches": solver.NumBranches(),
    }
    return Plan(method, {p.id: float(solver.Value(si)) for si, p in zip(s, P)}, info=info)


# --- 4. Dinkelbach: maximize a ratio with a sequence of linear problems ---------------------------


def max_blended(brief: Brief, solve, method: str) -> Plan:
    """Lexicographic: maximize spend, then maximize the blended rate among plans that spend it.

    The blended rate N(s)/D(s) (outcomes / impressions) isn't linear. Dinkelbach's method:
      1. start with lambda = the blended rate of some feasible plan;
      2. solve the LINEAR problem: maximize N(s) - lambda * D(s);
      3. set lambda = N/D of that solution; repeat until step 2's optimum is ~0.
    Each step is an ordinary MILP (or LP per subset, for brute force). It converges in a few steps.
    `solve(brief, weights, min_spend)` is `solve_milp` or `brute_force`.
    """
    P = brief.products
    spend_plan = solve(brief, np.ones(len(P)), 0.0)  # stage 1: the most that can be spent
    if not spend_plan.feasible:
        return Plan(method, feasible=False)
    min_spend = spend_plan.total_spend(brief) - 0.01
    w_out = np.array([p.per_dollar for p in P])
    w_imp = np.array([p.imps_per_dollar for p in P])
    plan, lam = spend_plan, spend_plan.blended(brief)
    for it in range(1, 50):
        nxt = solve(brief, w_out - lam * w_imp, min_spend)
        value = nxt.outcomes(brief) - lam * nxt.impressions(brief)
        plan = nxt
        if value <= 1e-9 * max(1.0, nxt.outcomes(brief)):
            break
        lam = nxt.blended(brief)
    plan.method = method
    plan.info = {**plan.info, "dinkelbach_iterations": it}
    return plan


# --- 5. the app's rule, for comparison -----------------------------------------------------------


def app_bundle(brief: Brief) -> Plan:
    """The app's bundle choice (rank-order fill, else the fallback search), ignoring single-first.

    `steps.best_bundle` is what the card shows as the bundle; the app's actual pick may be a
    single product (single-first). Both are returned by `app_pick`.
    """
    plan = _app_plans(brief)[1]
    if plan is None:
        return Plan("app bundle (rank fill)", feasible=False)
    return Plan("app bundle (rank fill)", {ln.product_id: ln.spend for ln in plan.lines})


def app_pick(brief: Brief) -> Plan:
    plan = _app_plans(brief)[0]
    if plan.kind == "none":
        return Plan("app pick (single-first)", feasible=False)
    return Plan("app pick (single-first)", {ln.product_id: ln.spend for ln in plan.lines})


def _app_plans(brief: Brief):
    slots = Slots(vertical=brief.vertical, kpi=brief.kpi, geo=brief.geo, budget=brief.budget, impression_goal=brief.goal)
    bench = steps.compute_benchmarks(conn(), brief.vertical)
    cands = steps.select_products(conn(), slots, bench)
    inv, sel = InventoryPolicy(), SelectionPolicy()
    scale = steps.check_scale(conn(), slots, cands, inv)
    rejections = steps.assess(cands, scale, slots, inv)
    return steps.build_plan(cands, scale, rejections, slots, inv, sel)


def outcome_weights(brief: Brief) -> np.ndarray:
    return np.array([p.per_dollar for p in brief.products])
