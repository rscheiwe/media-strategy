# Findings: when to run the workflow vs. converse

**Setup:** 40 labelled conversations (38 scored, 2 ambiguous) × 5 trials × 3 variants, `gpt-4o-mini` for every model call, temperature 0, vel 0.5.2. Two full runs, about $0.45 total. Raw run outputs aren't included in the repo.

## Results (run 2, after fixes)

| metric | v1 prompt-only | v2 router + gate + forced tool | v3 router + gate + direct dispatch |
|---|---|---|---|
| turn accuracy | 93.3% | **100%** | **100%** |
| trigger recall | 83.3% | **100%** | **100%** |
| false triggers | 0% | 0% | 0% |
| argument accuracy | 100% | 100% | 100% |
| conversations passing all 5 trials | 35/38 | **38/38** | **38/38** |
| latency p50 / p95 | 0.9s / 3.4s | 2.2s / 4.3s | 2.1s / 3.2s |
| tokens per turn | 1,347 | 2,245 | 1,787 |
| invented recommendations | 0 | 0 | 0 |

Run 1, before the fixes below, had v1 at 88.9% with a **7.4% false-trigger rate** (it called the tool while a run was in progress) and v2/v3 at 96%.

## Latency (run 3, plus a v4 rerun with the reply guard)
What the user waits for, not total turn time. **First visible** is the first thing on screen (status, text, tool or stage). **First content** is the first real text, workflow stage or tool call.

| p50 / p95 | v1 prompt-only | v3 router + dispatch | **v4 = v3 + fast path** | v4 with `gpt-4.1-nano` router |
|---|---|---|---|---|
| first visible | 0.45s / 0.70s | 1.25s / 1.66s | **0.00s / 0.00s** | 0.00s / 0.00s |
| first content | 0.45s / 0.70s | 1.25s / 1.66s | **1.09s / 1.57s** | 0.90s / 1.36s |
| first content, chat turns | 0.42s | 1.35s | 1.13s | 1.01s |
| first content, run turns | 0.49s* | 0.96s | 1.02s | 0.79s |
| router time | — | 0.95s | 0.98s | 0.77s |
| accuracy / all-5-trials | 94.2% / 35/38 | 100% / 38/38 | **100% / 38/38** | 91.6% / 34/38 |

\* v1's first content on a run turn is the model streaming its tool call; the first workflow stage comes later.

**v4** keeps v3's decisions and adds three things:
1. A transient `data-status` part goes out the moment the request arrives.
2. The router is skipped where its answer can't matter: a run is in progress, or the message is plain small talk with no campaign details. That was 35 of 200 turns.
3. Otherwise a tool-less reply is drafted **in parallel** with the router, then either used (chat/discuss turns, 75) or discarded (run/ask turns, 125). Before it's sent, a code guard rejects any draft that names products as a recommendation without a workflow result behind it. The guard was added after run 3, where the draft invented "the latest recommendation" twice on a "don't run it yet" brief. After the fix: 0 invented recommendations.

**What's left:** the router call (about 1.0s with `gpt-4o-mini`) sets the floor for real content. v4 makes the wait visible from 0ms and is about 0.2s faster than v3, but chat-turn content still trails v1 by about 0.7s. A faster router model isn't the answer: nano saved 0.2s and cost 8 points of accuracy and 4 conversations. Remaining options:
- **Route and reply in one call:** structured output with the reply as a field, streamed as partial objects. Chat content would arrive at roughly first-token time plus the routing fields, perhaps 0.6s. More complex, and the reply is drafted before the gate decides.
- **Put a ≥1,024-token static prefix first** in the router prompt, so OpenAI's automatic prompt caching applies, which modestly lowers first-token time.
- **Use the button/form path** for answering missing details. A typed answer needs no router at all.

## What failed and why
- **v1 (the decision is left to the model):** it missed 15 of 90 trigger turns, all the same way. The model that has to extract the details also has to decide, and under an "ask if anything is missing" rule it errs towards asking:
  - "optimizing for clicks" and "strong viewability" were not read as a KPI, so it asked for the KPI again (5/5 and 4/5 trials).
  - "Run the same plan for EMEA" got a question about the KPI, which was already in the earlier result (5/5).

  In run 1, without a state flag in its context, it also tried to run again while a run was in progress (10/10). Better prompting fixed that, but each such fix is one more rule the model has to follow every time.
- **v2/v3, run 1:** on the turn that supplied the last missing detail ("US, $30k, CTR"), the router quoted the *earlier* request as evidence. The evidence check rejected the quote and dropped the turn to chat, and the chat agent then **invented a recommendation in plain text**.
  - **Fix 1:** a turn without evidence in the latest message may continue a pending request but not start a new one.
  - **Fix 2:** non-run turns are told outright that products and numbers come only from the workflow.
  - After both fixes: 100%, and no invented recommendations.

## Ambiguous cases (product decisions, not scored)
- **"What would change if the budget were $40k?"** (after a result): v1 re-ran it 5/5 times; v2/v3 treated it as a question about the earlier result 5/5 times, and answered without new numbers. Pick a policy:
  - If what-ifs should re-run, the router prompt should classify "what if <changed detail>" as `request_recommendation`.
  - Alternatively, a cheap follow-up `what_if` tool can recompute the deterministic steps without the full workflow.
- **"If a QSR brand had $15k for the US, would you lean towards video?":** no variant ran it.

## Recommendation for the case study
1. **Use v4: v3 (router + code gate + direct dispatch) plus the fast path** (instant status, router skips, a guarded parallel draft). It has the same 100% decisions as v3, shows something at 0ms, and is the cheapest of the router variants.
   The v3 rationale below still applies: It matches v2 on every quality metric, with fewer tokens and a lower p95 latency. The workflow's arguments come from validated code, not from a model filling a tool call. The main agent never decides whether to run anything; it only chats, asks for missing details, or narrates the result. This is the deterministic version of a form-tool handshake, and it maps directly onto mesh conditional edges: router → gate → dispatch / ask / chat.
2. **The router is a classifier, not a decision-maker.** It reports intent plus details plus a verbatim evidence quote, and code checks the quote, validates the details and checks the state flags (`pending_request`, `workflow_running`, `last_result`).
3. **Keep the protections in place:**
   - the tool validates its own arguments
   - an idempotency key
   - one run at a time
   - "no products or numbers outside workflow results" in every non-run turn
4. **Run this eval on every prompt or model change, with k=5.** Single-trial runs hide the inconsistency. Grow the set from real failures.
5. **v2 is worth keeping** if a visible "the agent called the tool" step matters, for example to show tool use in the deck. v1 is a useful baseline slide: same model, same information, but the decision is left to the prompt.

## Caveats
- One model (`gpt-4o-mini`) and one domain. A stronger model would likely narrow the gap with v1, at a higher cost. A comparison on `gpt-4.1-mini` / `gpt-5-mini` would cost about $5–15.
- The eval set is hand-written: 40 conversations, 51 user turns.
- Asks are scored by keyword matching, and invented recommendations by a product-name heuristic.
- The upstream analyst stage ran in template mode during the eval. `ANALYST_MODE=llm` makes it a real LLM call.
