# Findings: prompt ablation. Do the behaviour rules earn their place?

**Question:** are we over-engineering, so that the rules cripple the LLM's ability to infer?

**Setup**
- Model: gpt-5.4-mini (the app default), using the real agent and tools.
- Runs: 5 per case, 2026-10-05.
- Cases (12): the QSR video question, 4 scope cases, and the 7 sample client briefs.
- Grading:
  - the judge is gpt-5.4, given truth computed from the raw CSVs, including derived figures (expected clicks, eCPC/vCPM, baselines elsewhere, line-item ranges), with about 1% rounding tolerance;
  - scope cases are checked deterministically.
- Plumbing is identical in every variant: fetch then compute, full error messages, untruncated output, percentages computed in Python, schema in the prompt.

Raw run outputs aren't included in the repo; `run_ablation.py` writes them to `results/`, and `summarize.py` rebuilds the summary.

| Variant | Fair pass (excludes leaked briefs 1, 3) | QSR | Scope | Briefs (fair) | KPI reading | Conclusion | Words p50 | Secs p50 |
|---|---|---|---|---|---|---|---|---|
| V0 current: 15 behaviour bullets + context recipe | 30/50 | 1/5 | 14/20 | 15/25 | 26/30 | 20/30 | 213 | 6.4 |
| V1 minimal: role, tools, schema, 2 guardrails | 29/50 | 2/5 | 15/20 | 12/25 | 24/30 | 19/30 | 182 | 5.9 |
| **V2** minimal + worked example (Retail/APAC) + geo example | **35/50** | 0/5 | 18/20 | **17/25** | 21/30 | 20/30 | 156 | 5.3 |
| V3 minimal + worked example (Entertainment/APAC) + geo example | 31/50 | **3/5** | 16/20 | 12/25 | **28/30** | 18/30 | 132 | 5.2 |
| V4 V3 + one rule ("judge by the vertical's numbers") | 29/50 | 0/5 | 16/20 | 13/25 | 27/30 | 20/30 | 134 | 5.0 |
| V5 minimal + both worked examples + geo example | 35/50 | 0/5 | 19/20 | 16/25 | 21/30 | 23/30 | 130 | 4.8 |

At this sample size, differences of about ±3–4 passes are noise.

## What it shows
1. **The behaviour rules didn't earn their place.** V0, with 15 bullets, is within noise of V1 (2 guardrails). Its answers are about 30 words longer and slower. That confirms the over-engineering concern: each rule was added after a handful of runs on one question.
2. **No single rule helps either.** Adding the best candidate back (V4) didn't improve on V3.
3. **Worked examples are the strongest lever** for format, number accuracy, scope handling and brevity: V2 and V5 are best on fair pass, with 130–156 words against 213.
4. **But examples transmit their conclusions, not just their method.** This is the most useful finding:
   - The Retail example says "Video Spotlight leads in-view" (true in Retail). QSR answers then leaned "video for in-view" in 4 of 5 runs, and QSR scored 0/5.
   - The Entertainment example shows Attention Builder beating video. It gave the best KPI reading and QSR 3/5, but weaker briefs.
   - Using both examples (V5), a QSR answer claimed "Commerce Connect has the top CTR", which is the Retail example's leader, not QSR's.
   - Two contrasting examples also made the model less willing to commit when the vertical was missing (brief 6: 0/5 correct conclusions, against 3/5 for V2).
5. **Leakage is unavoidable with real data,** because benchmarks don't depend on geo. Any real example reveals its vertical's rankings. The "fair" column excludes the leaked briefs for every variant.
6. **The QSR trap is not solved by prompting this model.** Every variant scored 0–3/5. The failure is weighing a vertical-specific outlier against general patterns. Earlier, reasoning effort (gpt-5.4-mini, effort low, via OpenRouter) helped on this question; see `experiments/model_compare/FINDINGS.md`.

## Decision (2026-10-05)
Decision: no reasoning effort; switch to V2.
- The app now ships V2 byte for byte (`app/api/src/chat_api/chat/prompts.py`). The tested text is frozen in `v2_prompt.txt`, and the old prompt in `v0_prompt.txt`.
- The recipe's context section was removed (`run_python_analysis_tool(context_recipe=False)`). The inventory yes/no flag was replaced by coverage %.
- No reasoning effort. The QSR trap stays a documented limit. A live rerun of the QSR question on V2 failed ("lean toward video only if in-view rate is the main KPI", against a table showing Social Amplifier at 78.72% vs 74.84%). It's kept as a known limitation, not re-scoped.

## Recommendation (as made before the decision)
- Replace the app's 15 behaviour bullets with the minimal prompt plus V2's examples, and drop the context recipe. That gives the best fair score with shorter, faster answers.
- Keep the two guardrails: "only numbers from the output" and "say when the data can't answer as asked".
- Treat examples as high-leverage, and choose them knowing what they teach.
- Document the QSR trap as a known limit of free-form analysis on this model.
