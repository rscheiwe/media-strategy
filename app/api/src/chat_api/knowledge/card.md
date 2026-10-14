# Reading the recommendation card

| Section | What it shows |
|---|---|
| Client requirements | The brief's vertical, KPI, geo, budget and goal |
| Recommended product / bundle | Each line's spend, forecast impressions, KPI rate and forecast confidence; a bundle's blended rate and any unspent budget |
| What $X delivers (pale yellow box) | The pick, and the bundle not chosen if there is one: rate, expected outcomes and cost per outcome |
| Why this is the best fit | The written rationale |
| Rejected top alternatives | Each product ranked above the pick, and why it wasn't chosen |
| Tradeoffs | Computed facts worth knowing about the pick |
| Calculated benchmarks and inventory | The full table behind the decision: every product's rates, CPM, impressions the budget buys, usable inventory and confidence |

## What $X delivers

- **Expected outcomes** = impressions × KPI rate: expected clicks for CTR, expected viewable impressions for in-view rate.
- **Cost per outcome** = spend ÷ outcomes: cost per click, or cost per 1,000 viewable impressions.
- These are for comparison only. The pick is decided by rate (see `selection`); this box shows what that choice costs or gains in volume.

## Why a product was rejected

| Reason | Meaning |
|---|---|
| Inventory below budget | Its usable inventory can't absorb what the budget buys |
| Budget below goal | The budget buys fewer impressions than the goal at this product's CPM |
| Inventory below goal | Its usable inventory is below the goal |
| Below minimum confidence | Its confidence is under the cut-off (threshold policy only) |

## Tradeoffs

| Tradeoff | Meaning |
|---|---|
| KPI leader not viable | The best product on the KPI can't carry the brief |
| Bundle uplift | A bundle would raise the blended KPI by 5% or more (shown with the bundle) |
| Near-tie | The runner-up is within 0.5 points of the pick |
| Low confidence line | A plan line has forecast confidence under 0.80 |
| Thin margin | Usable inventory covers the buy by less than 5% |
| Unspent budget | Budget left over, with the reason: under the $2,000 line minimum, at the 3-product limit, or no inventory left |
| Goal margin | How far the plan exceeds or misses the impression goal |
| No viable plan | Nothing works for this brief |

## Who wrote what

- **Every number, the pick, the rejections and the tradeoffs** are computed by code.
- **The rationale** ("Why this is the best fit" and the per-product explanations) is written by a model from those finished results. It must explain every product ranked above the pick; if it fails a check, template sentences are used instead.

## Same brief, same answer

A repeated brief replays the stored result instead of recomputing. Changing any detail (for example, the budget) runs the workflow again. Every card records the inventory and selection policies it ran under.
