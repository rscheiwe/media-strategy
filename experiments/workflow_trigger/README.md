# Workflow-trigger experiment

How consistently can a chat agent decide **when to run a workflow** and when to just converse? A main agent chats, and a tool calls another API, behind which a multi-stage workflow runs. The tool streams each stage back through the main API to an AI SDK frontend.

## Pieces
| File | Role |
|---|---|
| `wt/upstream.py` | The "other API". `POST /runs` streams SSE stage events: benchmarks → scale → viability → allocate (on the real case-study data) → analyst (an LLM call when `ANALYST_MODE=llm`, otherwise a template). |
| `wt/workflow_tool.py` | vel async-generator tool `run_recommendation`. Validates its arguments in code, proxies the upstream stream, and emits `data-workflow-stage` parts with stable ids (one row per stage, updated in place). |
| `wt/router.py` | A structured-output classifier. Returns intent, campaign details, and an evidence quote. It decides nothing. |
| `wt/gate.py` | Code that picks the turn's action: run, ask (for exactly the missing details), discuss an earlier result, status, or chat. |
| `wt/variants.py` | The three strategies compared (below). |
| `wt/server.py` | `POST /chat`: the main API, an AI SDK UI message stream (`x-vercel-ai-ui-message-stream: v1`). |
| `evals/evalset.yaml` | 40 labelled conversations: should call / ask / not call, follow-ups, mid-run, and ambiguous. |
| `evals/run_eval.py` | Runs variant × case × k trials. Writes `results/<time>/report.md`. |

## Variants
- **v1, prompt-only:** one agent, the tool always offered, `tool_choice=auto`. The prompt carries the trigger policy, negatives and examples.
- **v2, router + gate + forced tool:** the main agent is offered the tool only when the gate says run, and is then forced to call it (vel ≥ 0.5.2 `tool_choice`).
- **v3, router + gate + direct dispatch:** when the gate says run, code runs the workflow and the agent narrates. The LLM never chooses the tool.

## Run
```bash
uv sync
uv run pytest                      # offline: scripted models, in-process upstream
# add OPENAI_API_KEY to experiments/workflow_trigger/.env (git-ignored)
uv run python -m evals.run_eval --only brief1_acme,greeting,brief6_missing_vertical --k 1   # smoke test
uv run python -m evals.run_eval --variants v1,v2,v3 --k 5                                   # full run
uv run uvicorn wt.server:app --port 8100                                                    # main API
```
Data comes from the case-study folder (`../../*.csv`, `client_briefs.json`).
