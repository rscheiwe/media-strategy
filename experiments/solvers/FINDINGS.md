# Findings: exact solvers vs brute force vs the app's rule

**Run:** 2026-10-12, all 420 briefs (5 verticals × 2 KPIs × 3 geos × 7 budgets × goal or none), default inventory policy (usable = forecast × confidence). Raw output: `results/20261012-185152/matrix.json` (gitignored).

## 1. The exact methods agree everywhere

| Check | Result |
|---|---|
| Maximum outcomes: brute force vs MILP (HiGHS) | 0 disagreements |
| Maximum outcomes: CP-SAT vs MILP | 0 disagreements (within whole-dollar rounding) |
| Maximum blended rate (Dinkelbach): brute force vs MILP | 0 disagreements |
| Feasibility | Identical across all methods: 390 feasible, 30 infeasible |
| MILP optimality gap | 0 on every brief: every answer proven optimal |

So at 6 products the brute force is a valid test oracle for a solver, which is the plan proposed in `docs/Q_n_A.md` 1.1.

**Solver behavior at this size:**
- **MILP:** every brief was solved at the root node of branch and bound (median 1 node, max 1). Presolve and the LP relaxation were enough, with no branching needed.
- **Dinkelbach:** converged in 2–3 iterations (median 2).

**Time for all 420 briefs:**

| Method | Maximize outcomes | Maximize blended rate |
|---|---|---|
| Brute force (an LP per subset) | 43.2s | 109.2s |
| MILP (HiGHS) | 7.0s | 24.6s |
| CP-SAT | 6.2s | n/a |

Brute force is about 6× slower even at 6 products, where it solves 41 LPs per brief. Its cost grows like *n*³ (about 20.8 million subsets at 500 products); the MILP stays one model.

## 2. Acme Shoes (Retail, CTR, US, $30,000)

| Plan | Lines | Clicks | Blended CTR |
|---|---|---|---|
| **Maximum clicks** (all exact methods) | Social Amplifier $15,048 + Commerce Connect $14,952 | **24,873** | 0.93% |
| **Maximum blended rate, spend first** (Dinkelbach, 3 iterations) | Commerce Connect $21,414 + Premium Takeover $8,586 | 19,920 | **1.18%** |
| App bundle (rank-order fill) | Commerce Connect $21,414 + Attention Builder $8,586 | 23,592 | 1.16% |
| App pick (single-first) | Premium Takeover $30,000 | 8,493 | 0.91% |

**Two-product LP** (Commerce Connect and Attention Builder only): the optimum is the corner (21,414, 8,586), 23,592 clicks. That's the same as rank-order filling.

**Shadow prices at that optimum:**
- budget: 0.711 clicks per extra dollar;
- Commerce Connect inventory: 0.106 per extra dollar of inventory;
- Attention Builder inventory: 0 (not binding).

## 3. The app's rule vs the optima (390 feasible briefs)

| Measure | Result |
|---|---|
| App pick (single-first): outcomes as a share of the maximum | Median **82.1%**, minimum 24.6%; within 0.1% of the maximum in 58 of 386 |
| …single-product picks only (146) | Median **77.1%**, minimum 24.6% |
| App bundle (rank-order fill): outcomes as a share of the maximum | Median **88.3%**, minimum 57.1%; within 0.1% of the maximum in 40 of 294 |
| App bundle: blended rate vs the maximum (spend first) | Median **0 points** below; maximum 14.2 points below |

**What this means:**
- **Rank-order filling is usually optimal for the blended rate.** Its median gap to the true maximum is zero, which fits its design: fill the best rate first.
- **It isn't optimal for volume.** The median rank-order bundle gets 88% of the maximum possible outcomes.
- **Single-first costs the most volume.** The median single pick gets 77% of the maximum, and the worst gets a quarter. Its job is simplicity, not volume (`docs/Q_n_A.md` 3.2), but the size of the tradeoff is now measured.

## 4. Finding: the app misses four feasible plans

In four briefs the app reports **no viable plan**, but a plan exists that meets every constraint:

| Brief | Plan the solvers find | Impressions | Unspent |
|---|---|---|---|
| QSR / CTR / APAC, $10,000, 800K goal | Social Amplifier $9,918 | 1,102,010 | $82 |
| QSR / in-view / APAC, $10,000, 800K goal | Social Amplifier (same inventory) | 1,102,010 | $82 |
| Entertainment / CTR / APAC, $10,000, 800K goal | Social Amplifier $9,659 | 1,073,250 | $341 |
| Entertainment / in-view / APAC, $10,000, 800K goal | Social Amplifier (same inventory) | 1,073,250 | $341 |

**Cause: an inconsistency in the app's rules,** not a solver artifact.
- **Single products get no slack.** A single product is viable only if its usable inventory absorbs the **whole** budget. Social Amplifier's $9,918 is short of $10,000, so it's rejected.
- **Bundles get $2,000 of slack.** "Budget left under $2,000 stays unspent" is accepted for a bundle.
- **No bundle can rescue these briefs.** The leftover ($82 or $341) is under the $2,000 line minimum, and in every other combination the first product absorbs the whole budget and leaves the next line empty.

The result: "no viable plan" for a brief a strategist would happily run at 99% of budget, well over the goal.

**Possible fix (not applied; a rules decision):** treat a single product as viable if it can take all but less than the $2,000 line minimum, reporting the remainder as unspent. That's the same slack bundles already get. Re-running `app/api/evals/run_matrix.py` and `run_policy.py` would show which picks change.

## 5. Takeaways

1. **A MILP solver reproduces brute force exactly and is already faster at 6 products.** At catalog scale it's the only viable exact method.
2. **Writing the objective down surfaces the tradeoffs.** Rank-order filling sits near the blended-rate optimum and well below the volume optimum; single-first gives up the most volume.
3. **Checking against the constraints found a rule inconsistency that two independent oracles missed.** Both `evals/truth.py` and the matrix oracle implement the app's rules (a procedure) faithfully, so they agreed with the app. This experiment checks against a declarative specification of a valid plan instead. The brute force here found the four plans as well as the solvers did: the difference is procedure vs specification, not solver vs brute force. The solver's contribution is that the same check still works at catalog scale. See `docs/OPTIMIZATION_SOLVERS.md` §7.2.
4. **A solver as the decision engine would be over-engineering at 6 products,** but it's worth having as an oracle now, and as the engine once products or constraints multiply (`docs/OPTIMIZATION_SOLVERS.md` §7.1).
