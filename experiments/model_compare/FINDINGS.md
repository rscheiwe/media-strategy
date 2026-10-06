# Findings: which model reads the analysis correctly, and where the wait goes

**Question:** "If a QSR brand had $15k for the US, would you lean toward video?"
**Setup:** the real chat agent (same prompt, tools and `run_python_analysis` sandbox), 4 runs per model, no temperature (model defaults). Run on 2026-10-05. Raw run outputs aren't included in the repo.

**The truth (from `case.db`):** for QSR, Social Amplifier leads on BOTH CTR (1.15%) and in-view rate (78.7%), and it's the cheapest per click ($0.78) and per 1k viewable impressions ($11.43). Video Spotlight has the lowest CTR (0.60%), a mid in-view rate (74.8%), and costs $3.00 per click and $24.05 per 1k viewable. The tool printed these numbers correctly in every run; what differs is whether the model reads them correctly.

## Result after fixing two of our own bugs
The first run's low scores were mostly self-inflicted:
1. **The prompt manufactured a tradeoff.** The rule "if no KPI is given, compare both, explain the tradeoff, ask which matters" pushed every answer into "for CTR do X, for in-view do Y". This data has no fork (Social Amplifier leads on both), so models filled the in-view branch with video. Several stated the right numbers and still concluded "lean video only if in-view matters".
   - **New rule:** compare both; if one product leads on both, say so plainly; explain a tradeoff and ask for the KPI only when the ranking really differs. Also: judge by the vertical's numbers, not catalog descriptions.
2. **Tool errors lost their cause.** The one-line error kept only the first line of the exception, and pandas puts the multi-line SQL first. So "no such column: vertical" was cut to "Execution failed on sql '", and the model gave up. The error is now collapsed to one line, not truncated.

| model | correct, before | correct, after | total p50 after |
|---|---|---|---|
| gpt-4o-mini | 2/4 | **4/4** | 6.7s |
| gpt-5.4-mini (effort none) | 1/4 | 3/4 | 4.3s |
| gpt-5.4-nano (effort none) | 2/4 | **4/4** | 6.9s |
| gpt-5.4-mini, effort low (OpenRouter) | 3/4 | **4/4** | 7.0s |
| gemini-3-flash-preview | 1/4 | **4/4** | 6.8s |
| deepseek-v4-flash | 4/4 | 4/4 | 73.5s |

Overall: **13/24 → 23/24 with no model change.** The grader's "commits a plan" flag rose, but the flagged answers say "lean away from video" or show "what $15k buys (not an optimization plan)": advice, not an allocation. Those are false positives.

## Correctness, first run (before the fixes)

| model | route | correct conclusion | first text p50 | total p50 | tokens in/out |
|---|---|---|---|---|---|
| gpt-4o-mini (old default) | OpenAI | 2/4 | 1.8s | 6.9s | 3.1k / 0.5k |
| gpt-5.4-mini (effort none) | OpenAI | 1/4 | 2.7s | 4.3s | 3.3k / 0.6k |
| gpt-5.4-nano (effort none) | OpenAI | 2/4 | 3.7s | 6.7s | 4.2k / 0.9k |
| gpt-5.4-mini, effort low | OpenRouter | 3/4 | 7.3s | 8.2s | 4.3k / 0.9k |
| gemini-3-flash-preview | OpenRouter | 1/4 | 3.5s | 6.6s | 7.3k / 1.0k |
| deepseek-v4-flash | OpenRouter | 4/4 | 0.8s* | **83.6s** | 6.9k / 1.1k |

\* A one-line preamble streams before the tool call; the answer comes after.

n=4 is small: treat this as a direction, not a ranking. The judge (gpt-5.4-mini) was unreliable on "invented numbers", flagging correct figures, so that column is left out. Its correct/incorrect verdicts matched a manual read of the sampled answers.

## Why models get it wrong: one product × vertical cell breaks the pattern
*Verified independently from the raw CSVs (ratio-of-sums per product × vertical), 2026-10-05.*

**The outlier is Social Amplifier (P004) in QSR, not Video Spotlight.**

| P004 Social Amplifier | Entertainment | Finance | **QSR** | Retail | Travel |
|---|---|---|---|---|---|
| in-view rate | 64.76% (6th of 6) | 65.05% (6th) | **78.72% (1st)** | 63.84% (6th) | 65.78% (6th) |
| CTR | 0.778% | 0.616% | **1.148% (1st)** | 0.757% | 0.686% |

- **In-view rate: unique.** In QSR it's 13.87 points above P004's mean in the other four verticals. No other product comes close: the next-largest single-vertical swing is Video Spotlight in Travel at 5.40 points, then Attention Builder in Entertainment at 4.97.
- **CTR: the largest swing, but not unique.** QSR is 0.44 points above P004's mean elsewhere. Premium Takeover in Finance is moderately close, at 0.37.
- **The pattern holds across line items, not just on average.** P004's 40 QSR line items range 76.1–81.0% in-view; Video Spotlight's range 72.0–76.9%. The two ranges don't overlap.

**This clashes with the catalog's descriptions. That's a tension, not a contradiction.**
- P004 is described as a "lightweight mobile unit for efficient reach and social-style engagement".
- Video Spotlight (P002) is "designed for attention and strong in-view performance". Across all verticals combined, P002 does have the highest in-view rate (77.02%), narrowly ahead of Attention Builder (76.41%).
- So the general signals (the descriptions, P004's record everywhere else) point one way, and the QSR-specific data points the other. For a QSR client, the vertical-specific benchmark is the evidence that applies.

**The apparent winner isn't fully viable.** In QSR/US, $15k buys:
- P004: 1,666,667 impressions at $9 CPM, against 1,661,000 available. P004 has the lowest forecast confidence of the six (inventory_risk 0.78).
- P006 Attention Builder: 1,071,429 against 1,298,000 (0.88).
- P002 Video Spotlight: 833,333 against 1,018,000 (0.88).

The brief defines inventory_risk as "a forecast confidence score from 0.0 to 1.0, where higher means more confidence", and says "you must decide how to handle the inventory_risk field". **Our policy choice**, not a fact in the data, is to discount availability by that confidence (available × inventory_risk). Under it:
- P004's 1,295,580 adjusted capacity can't absorb the budget;
- P006 (1,142,240) and P002 (895,840) both fit.

**It lines up with a sample brief. This is an inference, not a stated fact.** Burger Bazaar is "a QSR advertiser running in the US with a $15,000 budget. They care most about in-view rate and want at least 800,000 impressions". (That's 800,000 impressions, not 800,000 in-view impressions.) The brief requires us to "explain tradeoffs when the apparent best product is not viable because of budget, desired impression goal, or available inventory". QSR/US reads as a test of two things:
- benchmarking per vertical rather than trusting general descriptions;
- then checking that the apparent best product is viable.

**What the wrong answers did.** They saw a correct QSR table and still concluded "lean toward video if in-view matters". In other words, they sided with the general pattern over the specific cell. Spot checks: gpt-5.4-mini scored 4/6 in `20261005-094329` and 3/4 in `20261005-095527`. This is experiment-grade evidence, not a fact about the data.

## Where the wait goes (`timeline.py`, one traced run each)
None of these runs used reasoning. The time is in sequential round trips:

| step | gpt-4o-mini | gpt-5.4-mini |
|---|---|---|
| model starts and picks the tool | 1.3s | 0.6s |
| **model streams the Python code** (tool arguments) | 2.0s | 1.5s |
| tool runs (subprocess + pandas) | 1.3s | 0.3s |
| a retry with a second code pass | +2.9s | — |
| second model call until text starts | 0.9s | 0.5s |
| answer streams | 3.4s | 1.8s |
| **total** | 11.9s | 4.7s |

Generating the code is the largest avoidable cost, followed by the second model call that reads the result. The sandbox itself is cheap (0.3–1.3s, with the first call paying the pandas import).

## Platform facts learned
- gpt-5, gpt-5-mini and gpt-5.1 accept function tools with `reasoning_effort` on `/v1/chat/completions`, so they work through vel's standard `openai` provider.
- **gpt-5.4, gpt-5.4-mini and gpt-5.5 reject that combination** ("use /v1/responses or set reasoning_effort to 'none'"). Route them through OpenRouter for tools and reasoning together, or run them at effort none.
- vel's `openai-responses` provider wasn't tested for this.

Rerun with:
- `cd app/api && uv run python ../../experiments/model_compare/compare.py --runs 4 [--only <label>]`
- `PYTHONPATH=../../experiments/model_compare uv run python ../../experiments/model_compare/timeline.py [labels]`
