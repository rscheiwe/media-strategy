# Selection: how the plan is chosen

## Ranking

Products are ranked by the brief's KPI **rate** (CTR or in-view rate) for the vertical, best first; ties go to the lower product ID so the order never changes. This is the literal reading of the case brief's "rank viable products by the requested KPI".

## The rules (default: single product first)

1. **Single product.** The highest-ranked product that can carry the brief alone (usable inventory covers the buy, and any impression goal is met) gets the whole budget. It can be a lower-ranked product if the leaders don't fit.
2. **Bundle, filled in rank order.** If no product fits alone: the #1 product takes as much budget as its usable inventory allows, then #2, then #3.
   - At most 3 products.
   - Each line must be at least $2,000; a product whose share would be smaller is skipped.
   - Budget left under $2,000 stays unspent.
   - The bundle must have at least 2 lines and meet the goal.
3. **Fallback search.** If rank-order filling misses the goal, every 2- and 3-product combination is tried. The winner spends the budget, then has the highest blended KPI, then the fewest lines.
4. **No plan.** If nothing works, the card says so and reports the whole budget unspent.

## Worked examples (default policies)

| Brief | What happens | Plan |
|---|---|---|
| Acme, Retail/CTR/US, $30,000 | Commerce Connect (#1) and Attention Builder (#2) can't take $30,000 alone; Premium Takeover (#3) can | Premium Takeover, $30,000, 937,500 impressions |
| Acme at $40,000 | Nothing fits alone | Bundle: Commerce Connect $21,414 (all its usable inventory), then Attention Builder $18,586 |
| Any vertical, $5,000, 800,000 goal | No product or combination reaches 800,000 impressions | No plan |

## Why single product first

- A single line is simpler to book, traffic and report, and the brief asks for "the best product or small bundle".
- The cost: a bundle can sometimes deliver more, at a higher rate. Acme is an example. Premium Takeover gets about 8,493 clicks at $3.53 per click; the Commerce Connect + Attention Builder bundle would get about 23,592 at $1.27, roughly 2.8× the clicks. The card shows that bundle as an alternative, and the "What $X delivers" box compares both.
- The alternative policy, **kpi_max**, picks whichever single product or bundle has the highest blended KPI; under it, Acme gets the bundle.

## Why fill bundles in rank order

Two optimizing rules were tried and rejected:
- **Maximizing the blended rate** added a pricey, low-volume third line just to lift the average.
- **Maximizing volume** (total clicks or viewable impressions) traded quality for cheap impressions.

Rank-order filling gives each dollar to the best-ranked product that still has room. It is a greedy heuristic, a deliberate compromise between those two objectives rather than the optimum of either. For Acme's bundle it gets about 95% of the maximum possible clicks at a much higher CTR than the click-maximizing plan.

## Other ways "best" could be defined

The ranking metric is a design choice. A strategist could reasonably rank by:

| Metric | Rewards | #1 for Acme |
|---|---|---|
| KPI rate (current) | Quality of each impression | Commerce Connect (1.23% CTR) |
| Expected volume, or cost per outcome | What the money buys | Social Amplifier |
| Deliverable volume (capped at usable inventory) | What can actually be delivered | Commerce Connect |

Today the rate decides, and volume and cost are shown on the card for comparison only.

## Limits

- **Large budgets can leave money unspent.** At $40,000 and up, the top three products can all be at their usable inventory; the 3-product limit then leaves the rest unspent, and the card reports it.
- **Single products get no slack, bundles get up to $2,000.** A single product must absorb the whole budget, while a bundle may leave under $2,000 unspent. So a product that could take, say, 99% of the budget is rejected when used alone.
- **Near-ties count as full ranks.** A product 0.1 points behind still ranks lower; the card flags near-ties (within 0.5 points) as a tradeoff.
- **Confidence doesn't change fill order.** A low-confidence product still fills first if it ranks first; its capacity is already discounted and the line is flagged.
