"""Selection-rules matrix: app picks vs an independent pandas implementation (README.md, "Selection rules").

cd app/api && PYTHONPATH=src:. uv run python -m evals.run_matrix
"""

import itertools
import math
import collections
import pandas as pd
from pathlib import Path
from chat_api.casedata import connect_readonly, ensure_case_db
from chat_api.workflow.domain import Slots
from chat_api.workflow import steps
from chat_api.workflow.steps import recommend

ROOT = str(Path(__file__).resolve().parents[1] / "case_data") + "/"  # app/api/case_data
h = pd.read_csv(ROOT + "campaign_history.csv")
p = pd.read_csv(ROOT + "product_catalog.csv").set_index("product_id")
inv = pd.read_csv(ROOT + "inventory_forecaster.csv")
MIN, MAXL = 2000.0, 3


def oracle(v, kpi, geo, budget, goal):
    g = (
        h[h.vertical == v]
        .groupby("product_id")[["impressions", "clicks", "viewable_impressions"]]
        .sum()
    )
    g["ctr"] = g.clicks / g.impressions
    g["in_view_rate"] = g.viewable_impressions / g.impressions
    g = g.join(p[["cpm"]]).join(
        inv[(inv.vertical == v) & (inv.geo == geo)].set_index("product_id")[
            ["available_imps", "inventory_risk"]
        ]
    )
    g["kpi"] = g[kpi]
    g["buys"] = [math.floor(budget / c * 1000 + 1e-9) for c in g.cpm]
    g["cap"] = [
        math.floor(a * r + 1e-9) for a, r in zip(g.available_imps, g.inventory_risk)
    ]
    order = sorted(g.index, key=lambda i: (-g.kpi[i], i))
    viable = [
        i
        for i in order
        if g.cap[i] >= g.buys[i] and (not goal or g.buys[i] >= goal) and budget >= MIN
    ]
    if viable:
        return ("single", [viable[0]], 0.0)
    rem, lines = float(budget), []
    for i in order:
        if rem < MIN or len(lines) == MAXL:
            break
        spend = round(min(rem, g.cap[i] * g.cpm[i] / 1000), 2)
        if spend < MIN:
            continue
        lines.append((i, spend, math.floor(spend / g.cpm[i] * 1000 + 1e-9)))
        rem -= spend
    if len(lines) >= 2 and (not goal or sum(ln[2] for ln in lines) >= goal):
        return ("bundle", [ln[0] for ln in lines], round(rem, 2))
    return ("fallback-or-none", None, None)


c = connect_readonly(ensure_case_db())
stats = collections.Counter()
mism = []
fallback_cases = []
unspent_big = []
orig = steps.rank_fill
fb = {"hit": False}


def spy(*a, **k):
    r = orig(*a, **k)
    fb["hit"] = r is None
    return r


steps.rank_fill = spy
for v, kpi, geo, budget, goal in itertools.product(
    ["Retail", "Finance", "Travel", "QSR", "Entertainment"],
    ["ctr", "in_view_rate"],
    ["US", "EMEA", "APAC"],
    [5000, 10000, 15000, 25000, 40000, 60000, 100000],
    [None, 800000],
):
    fb["hit"] = False
    rec = recommend(
        c, Slots(vertical=v, kpi=kpi, geo=geo, budget=budget, impression_goal=goal)
    )
    plan = rec.plan
    stats["briefs"] += 1
    stats[plan.kind] += 1
    ids = [ln.product_id for ln in plan.lines]
    # invariants
    assert plan.total_spend <= budget + 0.01
    for ln in plan.lines:
        s = next(x for x in rec.scale if x.product_id == ln.product_id)
        assert ln.impressions <= s.capacity, (v, kpi, geo, budget, ln)
        assert ln.spend >= MIN - 0.01
    assert len(plan.lines) <= MAXL
    if goal and plan.kind != "none":
        assert plan.total_impressions >= goal
    kind, oids, orem = oracle(v, kpi, geo, budget, goal)
    if plan.kind == "bundle" and fb["hit"]:
        fallback_cases.append((v, kpi, geo, budget, goal, ids))
        stats["fallback used"] += 1
    elif kind in ("single", "bundle"):
        if (plan.kind, ids) != (kind, oids):
            mism.append(((v, kpi, geo, budget, goal), (plan.kind, ids), (kind, oids)))
    elif plan.kind != "none" and not fb["hit"]:
        mism.append(((v, kpi, geo, budget, goal), (plan.kind, ids), "oracle: none"))
    if plan.kind != "none" and plan.unspent >= MIN:
        unspent_big.append((v, kpi, geo, budget, goal, plan.unspent))
print(dict(stats))
print("mismatches vs independent rules:", len(mism))
for m in mism[:8]:
    print("  ", m)
print(
    "fallback (rank fill missed the goal, best combination used):", len(fallback_cases)
)
for f in fallback_cases[:8]:
    print("  ", f)
print("plans leaving >= $2,000 unspent:", len(unspent_big))
for u in unspent_big[:8]:
    print("  ", u)
