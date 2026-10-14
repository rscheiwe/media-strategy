# Inventory: forecast, confidence and usable inventory

## The two inputs

**The forecast (`available_imps`)** estimates how many impressions of a product will be available, for one vertical in one geo. Example: Commerce Connect, Retail, US: 1,720,000.
- It's an estimate: ad inventory is future traffic, not stock on a shelf, so real delivery can come in lower or higher.
- The data has no time dimension, so the whole forecast is treated as available to this campaign.

**The confidence (`inventory_risk`)** says how much to trust that forecast, from 0 to 1. **Higher means more trustworthy**, despite the field's name. In this data it runs from 0.72 to 0.94.
- It's independent of the forecast's size. In QSR/US, Social Amplifier has the bigger forecast (1,661,000) but the lowest confidence (0.78); Attention Builder's is smaller (1,298,000) but more trustworthy (0.88).

## The rule: usable inventory = forecast × confidence

The case brief says how `inventory_risk` is used must be decided. The default rule is:

> **Usable inventory = forecast × confidence** (rounded down)

Example: Commerce Connect, Retail/US: 1,720,000 × 0.83 = 1,427,600 usable.

A product can carry a brief on its own only if its usable inventory covers the impressions the budget buys (budget ÷ CPM × 1,000).

**A bigger forecast isn't always more usable.** In Retail/US:

| Product | Forecast | Confidence | Usable inventory |
|---|---|---|---|
| Commerce Connect | 1,720,000 | 0.83 | 1,427,600 |
| Attention Builder | 1,593,000 | 0.93 | 1,481,490 |

Commerce Connect has 127,000 more impressions forecast, but Attention Builder's forecast is more trustworthy, so it ends up with more usable inventory. This happens in 14 product pairs across the data; the widest gap is Travel/US, where Social Amplifier (1,888,000 at 0.77) ends up with less usable inventory than Commerce Connect (1,593,000 at 0.94).

## Why multiply

- **It's the expected-value reading.** At 0.83 confidence, plan on 83% of the forecast. A product fits only if it can deliver the buy in expectation.
- **It uses the field.** Ignoring confidence would dodge the question; multiplying is the simplest rule that lets confidence change the answer.
- **It errs on the safe side, where the costs are uneven.** Over-booking promises impressions that never deliver, which damages the client relationship. Under-booking only shifts the product mix. A rule that can only shrink the forecast leans the right way.
- **It has no cliff.** A cut-off (say, trust it fully at 0.80, ignore it at 0.79) would make a 0.01 difference flip a product from fully usable to useless. Multiplying scales smoothly.
- **It was tested, and it's a setting.** The alternatives below rarely change the pick, and the rule can be changed without code changes. Every recommendation records the policy it ran under.

**The caveat.** This reads confidence as "the share of the forecast that will deliver", which is an interpretation. If confidence means "the probability the forecast holds", multiplying assumes nothing delivers when it doesn't hold, which is pessimistic; the uneven costs above make that acceptable. A better rule would be calibrated against the history of forecast vs. actual delivery, which the case data doesn't include.

## What confidence does and doesn't affect

- It changes **how much** of a product can be bought, so it decides which products can carry the brief.
- It does **not** change how good a product is: ranking uses past performance only.
- A plan line with confidence under 0.80 is flagged on the card as low confidence (a warning, never a rejection).

## Alternatives that were tested

| Rule | Usable inventory |
|---|---|
| raw | the forecast, ignoring confidence |
| **adjusted (default)** | forecast × confidence |
| threshold 0.80 | the forecast if confidence ≥ 0.80, else 0 |
| adjusted + 10% headroom | forecast × confidence, keeping 10% unbooked as a safety buffer |
| adjusted, absorb 98% | a product fits if it covers 98% of the buy |

Each rule was run on the sample briefs. What it showed:
1. **How confidence is read rarely changes the answer.** Raw, adjusted and threshold give the same pick for all 5 complete sample briefs under the default selection rule.
2. **A safety buffer is what moves picks.** 10% headroom changes 4 of the 5 sample briefs, because several picks fit with very little to spare (Acme's Premium Takeover by 0.32%).
3. **The selection rule can matter more than the inventory rule.** Picking the highest blended KPI instead of single-product-first changes Acme's pick under every inventory rule.

In one line: how confidence is read rarely changes the answer; how much safety margin is kept does. That margin is a business judgment the strategist should own.

## Limits

- Nothing models other campaigns competing for the same inventory; headroom is the only lever, and it's a flat percentage.
- Each product is tested alone against the whole budget. Splitting the budget across products happens in the plan.
