# Routing eval: A (planner-tool) vs B (planner-router), k=1

**Decision rule:** n/a (need both approaches)

| metric | A |
|---|---|
| turn_accuracy | 91.8% |
| trigger_recall | 100.0% |
| args_accuracy | 100.0% |
| ask_accuracy | 100.0% |
| false_triggers | 2 |
| false_trigger_rate | 7.1% |
| silent_turns | 3 |
| invented_recommendations | 0 |
| slot_hallucinations | 0 |
| errors | 1 |
| pass_k | 47/52 |
| first_visible_p50_s | 0.56 |
| first_visible_p95_s | 0.95 |
| first_content_p50_s | 0.77 |
| first_content_p95_s | 1.18 |
| latency_p50_s | 2.13 |
| latency_p95_s | 3.29 |
| llm_calls_per_turn | 1.61 |
| llm_calls_by_caller | {"chat": 77, "explain": 26} |
| tokens_per_turn | 7017 |
| cost_usd | None |

## By category

| category | A |
|---|---|
| analysis | 6/6 |
| follow_up | 4/7 |
| mid_run | 0/2 |
| should_ask | 15/15 |
| should_call | 16/16 |
| should_not_call | 15/15 |

## Ambiguous (not scored)

- **A**: ambiguous_what_if_budget#0: ran, ambiguous_hypothetical_brief#0: form, app_bank_inferred#0: ran

## Failures

- **A** `after_why_split` turn 0 trial 0: false trigger. user: "Why is it split across two products?" reply: ""
- **A** `after_thanks` turn 0 trial 0: error: No response from LLM. user: "Great, thanks!" reply: ""
- **A** `running_is_it_done` turn 0 trial 0: silent turn (tool not_run: already_running). user: "Is it done yet?" reply: ""
- **A** `running_run_again` turn 0 trial 0: silent turn (tool not_run: already_running). user: "Run it again." reply: ""
- **A** `app_after_explain_leader` turn 0 trial 0: false trigger. user: "Why not Commerce Connect? It has the best CTR." reply: ""
