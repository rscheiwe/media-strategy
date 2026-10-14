# The recommendation workflow: 8 stages

A brief runs through eight stages, in a fixed order. Seven are deterministic code over the case data. Only the last, explain, calls a model, and it only writes words around results that are already computed.

```
validate ─┬─ details missing, or need confirming → form (the run stops and asks)
          └─ ok → benchmarks → select → scale → viability → plan → alternatives → explain
```

Apart from the form branch, it is a straight line: each stage uses the output of the one before.

Before the workflow starts, a router model reads the message and extracts the brief's details (vertical, KPI, geo, budget, goal). Code then checks them: a vertical or KPI the user never actually wrote is not trusted and goes on the form to confirm.

| # | Stage | The strategist's question | What it does |
|---|---|---|---|
| 1 | validate | Do I know enough to answer? | Checks that vertical, KPI, geo and budget are present and allowed. If not, returns a pre-filled form. |
| 2 | benchmarks | What has worked in this industry before? | Computes each product's CTR and in-view rate from past campaigns for the vertical. |
| 3 | select | How much does the budget buy of each product? | Ranks products by the KPI rate and computes the impressions the budget buys at each CPM. |
| 4 | scale | Is there enough of it in this region? | Computes usable inventory (forecast × confidence) for the geo and tests whether it covers the buy. |
| 5 | viability | Which products could do the job alone, and why not the others? | Gives each product a pass or a list of reason codes with numbers. |
| 6 | plan | What's the best plan I can actually deliver? | Picks a single product, builds a bundle, or reports no viable plan. |
| 7 | alternatives | What am I giving up? | Lists higher-ranked products not chosen, the bundle not chosen, and computed tradeoffs. |
| 8 | explain | How do I explain this to the client? | A model writes the rationale from the finished results, with a template fallback. |

## Each stage in short

**1. validate.** The vertical, KPI and geo must each be one of the allowed values, and the budget must be positive. An unknown value such as "LATAM" counts as invalid, not just missing. Anything missing or unconfirmed goes to the form, pre-filled with what's known.

**2. benchmarks.** The only stage that looks at the past. For the client's vertical, it adds up every past line item per product: CTR = total clicks ÷ total impressions; in-view rate = total viewable impressions ÷ total impressions (a ratio of sums, see `benchmarks`). It ignores geo (campaign history has none), budget and time.

**3. select.** Ranks all six products by the KPI rate, best first, and divides the budget by each product's CPM: impressions bought = budget ÷ CPM × 1,000, rounded down. Nothing is removed here: a product that won't fit keeps its rank, so the card can explain why it lost.

**4. scale.** The only stage where geo matters. For each product on its own, usable inventory = forecast × confidence, compared with the impressions the budget buys. Cheap products fail most often, because a low CPM buys many impressions. See `inventory`.

**5. viability.** A product can carry the brief alone if its usable inventory covers the buy and, when there's an impression goal, both the buy and the inventory reach it. Each failure is a reason code with its numbers (for example "short 572,400 impressions"). These become the card's rejection reasons.

**6. plan.** If any product can carry the brief alone, the plan is the highest-ranked of those, even if it isn't #1. Otherwise it builds a bundle of up to 3 products, filled in rank order with a $2,000 minimum per line; if that misses the goal, it searches every 2- and 3-product combination. If nothing works, there is no viable plan. See `selection`.

**7. alternatives.** Assembles what the card and rationale need: each product ranked above the pick and why it lost, the runner-up, the best bundle if it beats the pick's KPI by 5% or more, and tradeoffs such as a thin margin or a near-tie. See `card`.

**8. explain.** A model gets the finished results as text (every number already formatted) and fills a structure built for this brief: a "best fit" statement plus one required explanation per product ranked above the pick. Code checks it names every product in the plan, retries once, and falls back to template sentences. The model never computes a number or changes the decision.

## Worked example: Acme Shoes (Retail, CTR, US, $30,000)

| Stage | Result |
|---|---|
| validate | All details present |
| benchmarks | 6 products benchmarked for Retail |
| select | Commerce Connect leads on CTR (1.23%) |
| scale | 6 checked in US; 5 can't absorb $30,000 |
| viability | 1 product viable alone: Premium Takeover |
| plan | Pick: Premium Takeover (#3 on CTR), $30,000, 937,500 impressions |
| alternatives | Commerce Connect and Attention Builder explained; tradeoffs: KPI leader not viable, a bundle would lift CTR 27.6%, thin margin (0.32%) |
| explain | Rationale written |

In words: Commerce Connect has the best CTR in Retail, but there isn't enough of it in the US for $30,000. Premium Takeover is the best option that can take the whole budget, though only just.

These figures are for the default policies. Same brief, same answer: a repeated brief replays the stored result rather than recomputing.
