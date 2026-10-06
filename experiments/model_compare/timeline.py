"""Where does the wait before the first answer text go? Traces one run per model with a
timestamp on every stream event, and splits the time into model-thinking gaps vs tool runs.

    cd app/api && uv run python ../../experiments/model_compare/timeline.py [labels...]
"""

from __future__ import annotations

import asyncio
import sys
import time

from compare import MODELS, QUESTION, make_agent  # noqa: E402  (same directory)


async def trace(label: str, provider: str, model: str, effort: str | None) -> None:
    agent = make_agent(provider, model, effort)
    t0 = time.monotonic()
    marks: list[tuple[float, str]] = []
    first_answer = None
    last_kind = None
    async for event in agent.run_stream({"messages": [{"role": "user", "content": QUESTION}]}, stateless=True):
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        now = time.monotonic() - t0
        if kind in ("start-step", "finish-step", "tool-input-start", "tool-input-available", "tool-output-available"):
            name = event.get("toolName") or ""
            marks.append((now, f"{kind} {name}".strip()))
        elif kind == "text-delta" and last_kind != "text-delta":
            marks.append((now, "text starts"))
        if kind == "text-delta" and str(event.get("delta") or "").strip():
            first_answer = now  # keeps updating; the last text block is the answer
        last_kind = kind
    total = time.monotonic() - t0

    tool_time = 0.0
    for i, (t, what) in enumerate(marks):
        if what.startswith("tool-input-available"):
            out = next((t2 for t2, w2 in marks[i:] if w2.startswith("tool-output-available")), t)
            tool_time += out - t
    steps = sum(1 for _, w in marks if w == "start-step")
    print(f"\n=== {label}: total {total:.1f}s, {steps} model calls, tool execution {tool_time:.1f}s, "
          f"model/network {total - tool_time:.1f}s")
    for t, what in marks:
        print(f"  {t:6.2f}s  {what}")


async def main() -> None:
    wanted = [a.lower() for a in sys.argv[1:]] or ["gpt-4o-mini", "gpt-5.4-mini", "gemini", "deepseek"]
    for label, provider, model, effort in MODELS:
        if any(w in label.lower() for w in wanted) and "(OR)" not in label:
            await trace(label, provider, model, effort)


if __name__ == "__main__":
    asyncio.run(main())
