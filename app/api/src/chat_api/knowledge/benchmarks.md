# Benchmarks: how product performance is measured

A benchmark is a product's historical CTR and in-view rate for one vertical, computed from the campaign history.

- **CTR** = total clicks ÷ total impressions.
- **In-view rate** = total viewable impressions ÷ total impressions.

## Ratio of sums, not the average of rates

Each past line item has its own rate. There are two ways to combine them:

| Line item | Impressions | Clicks | Its CTR |
|---|---|---|---|
| A | 100,000 | 1,000 | 1.0% |
| B | 1,000 | 30 | 3.0% |
| Average of rates | | | (1.0% + 3.0%) ÷ 2 = 2.0% |
| Ratio of sums | 101,000 | 1,030 | 1,030 ÷ 101,000 = 1.02% |

The average of rates gives the tiny line item B the same say as A, which delivered 100 times the impressions. The ratio of sums weights each line item by its impressions, so it is the rate across all the impressions actually delivered. That is what a buy of impressions should be benchmarked against, and it is the standard way to pool rates.

In this data, line items are similar in size, so the two methods differ by at most 0.16 points and never change a product's rank. The choice matters for correctness on real, uneven data.

## What benchmarks ignore

- **Geo:** the campaign history has no geo column, so a product's benchmark is the same in every region. Only inventory varies by geo.
- **Budget:** past performance doesn't depend on this client's spend.
- **Time:** there are no dates, so every line item counts equally.

Each product has 40 line items in each vertical (1,200 in total).

## How benchmarks are used

- They set the ranking: products are ranked by the brief's KPI rate, best first.
- A bundle's blended rate is pooled the same way: the impression-weighted rate across its lines.
- Benchmarks say how good a product is; they never depend on inventory or forecast confidence.

Example, Retail: Commerce Connect has the best CTR (1.23%) and Video Spotlight the best in-view rate (75.32%).
