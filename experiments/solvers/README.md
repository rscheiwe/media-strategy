# Optimization solvers on the bundle problem

A hands-on companion to `docs/OPTIMIZATION_SOLVERS.md`. It solves the app's bundle problem (pick up to three products and split the budget) with exact methods, and compares them with the app's own rule. It does not modify `app/`.

The inputs come from the app's workflow code (`chat_api.workflow.steps`), so the benchmarks, CPMs and usable inventory are exactly what the recommendation card uses. Only `pydantic` is needed from the app's dependencies; no API key, no model calls.

## Run

```bash
cd experiments/solvers
uv run python acme.py      # one brief, step by step (a few seconds)
uv run python matrix.py    # all 420 briefs (about 3 minutes; --limit N for a smoke test)
```

`matrix.py` writes `results/<timestamp>/matrix.json` (gitignored). Findings are in `FINDINGS.md`.

## What's here

| File | Contents |
|---|---|
| `model.py` | The problem and every method: `brute_force` (an LP per subset), `solve_milp` (HiGHS via `scipy.optimize.milp`), `solve_cpsat` (OR-Tools CP-SAT, whole dollars), `max_blended` (Dinkelbach's method over either), and the app's own pick and bundle |
| `acme.py` | Acme Shoes walked through: the inputs, a two-product LP with its corners, shadow prices, three exact methods, the blended-rate optimum, the app's answers, and SAT as a 0-1 integer program |
| `matrix.py` | All 420 briefs: do the exact methods agree, and how does the app's rule compare with the optima? |

## The model

Per product *i*: spend *sᵢ* ≥ 0 and *yᵢ* ∈ {0, 1} (in the plan or not).

| Rule | Constraint |
|---|---|
| Budget | Σ *sᵢ* ≤ *B* |
| Usable inventory | *sᵢ* ≤ *Mᵢ* · *yᵢ*, with *Mᵢ* = capacity × CPM / 1000 |
| $2,000 line minimum | *sᵢ* ≥ 2000 · *yᵢ* |
| At most 3 products | Σ *yᵢ* ≤ 3 |
| Impression goal | Σ *sᵢ* · 1000 / CPMᵢ ≥ goal |

Objectives:
- **Outcomes:** maximize expected clicks (CTR) or viewable impressions (in-view rate). This is linear.
- **Blended:** maximize spend, then the blended rate among plans that spend it. The rate is a ratio, so it's solved with Dinkelbach's method, a few linear solves in sequence.

## Differences from the app's rules

These are differences in definition, not bugs in either side:
- **Underspending.** The solvers allow any plan within the budget. The app requires a single product to absorb the **whole** budget, while letting a bundle leave up to $2,000 unspent. See `FINDINGS.md`: this makes the app miss four feasible plans.
- **Rounding.** Spend and impressions are continuous here (whole dollars for CP-SAT); the app rounds spend to cents and impressions down.
- **Objective.** The app picks by rule (single-first, then rank-order fill), not by optimizing a stated objective. The comparison shows what that rule gives up against each optimum.
