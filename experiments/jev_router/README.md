# Jev workflow-router experiment

This experiment evaluates TypeSafe Jev as a bounded intent router for the 
media-recommendation workflow. It does not modify `app/`.

Jev classifies a turn; it does not extract workflow arguments, generate replies,
or execute tools. The intended production shape is:

```text
message + conversation state -> Jev route -> code gate -> chat / ask / confirm / run
                                                |
                              separate slot extraction and validation
```

The evaluator reuses `experiments/workflow_trigger/evals/evalset.yaml`, sends
each turn to the OpenRouter Decisions API, and reports route accuracy,
confidence, latency, usage, and errors. The workflow-trigger experiment remains
the baseline.

## Run

Keep the key in the environment; do not put it in a file or commit it.

```bash
cd experiments/jev_router
uv run python eval_jev.py --k 1
```

Use `--k 5` for the comparable repeated-trial run. The default model is the
pinned `typesafe/jev-1.13`; override it with `JEV_MODEL` if needed.

Outputs are written to a timestamped directory under `results/` and contain no
API key. The raw state is not persisted by default; only bounded metadata,
answers, and expected labels are recorded.

