"""Model comparison on the exploratory question that gpt-4o-mini got wrong.

Runs the real chat agent (chat_api.chat.agent.create_chat_agent: same prompt, same
tools, same run_python_analysis sandbox) on one question, N times per model, and
records what the user would see: time to first text, total time, tool calls, tokens.
A judge model then grades each answer against the true benchmarks from case.db.

    cd app/api
    uv run python ../../experiments/model_compare/compare.py [--runs 4] [--only substr]

Keys come from app/.env (OPENAI_API_KEY, OPENROUTER_API_KEY). Results go to
experiments/model_compare/results/<timestamp>/.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

HERE = Path(__file__).resolve().parent
load_dotenv(HERE.parents[1] / "app" / ".env")

from chat_api.casedata import connect_readonly, ensure_case_db  # noqa: E402
from chat_api.chat.agent import create_chat_agent  # noqa: E402

QUESTION = "If a QSR brand had $15k for the US, would you lean toward video?"

# (label, APP_CHAT_PROVIDER, APP_CHAT_MODEL, APP_CHAT_REASONING_EFFORT)
MODELS = [
    ("gpt-4o-mini (old default)", "openai", "gpt-4o-mini", None),
    # Direct from OpenAI, gpt-5.4 accepts function tools on /chat/completions only with
    # reasoning effort "none" (the default when unset); reasoning + tools needs /v1/responses,
    # which vel 0.5.2's openai-responses provider can't drive (tool shape, no effort passthrough).
    ("gpt-5.4-mini (default)", "openai", "gpt-5.4-mini", None),
    ("gpt-5.4-nano", "openai", "gpt-5.4-nano", None),
    # OpenRouter translates to the Responses API itself, so reasoning works there.
    ("gpt-5.4-mini low (OR)", "openrouter", "openai/gpt-5.4-mini", "low"),
    ("gemini-3-flash", "openrouter", "google/gemini-3-flash-preview", None),
    ("deepseek-v4-flash", "openrouter", "deepseek/deepseek-v4-flash", None),
]

JUDGE_MODEL = "gpt-5.4-mini"

ENV_KEYS = ("APP_CHAT_PROVIDER", "APP_CHAT_MODEL", "APP_CHAT_REASONING_EFFORT")


def make_agent(provider: str, model: str, effort: str | None):
    """Agents read their model from the environment when created; creation is
    synchronous, so set, create, restore, all before the next await."""
    saved = {k: os.environ.get(k) for k in ENV_KEYS}
    os.environ["APP_CHAT_PROVIDER"] = provider
    os.environ["APP_CHAT_MODEL"] = model
    if effort:
        os.environ["APP_CHAT_REASONING_EFFORT"] = effort
    else:
        os.environ.pop("APP_CHAT_REASONING_EFFORT", None)
    try:
        return create_chat_agent(api_key=os.getenv("OPENAI_API_KEY"))
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


async def run_once(provider: str, model: str, effort: str | None) -> dict:
    agent = make_agent(provider, model, effort)
    started = time.monotonic()
    first_text = None
    text, tools, usage, errors = [], [], {}, []
    try:
        async for event in agent.run_stream(
            {"messages": [{"role": "user", "content": QUESTION}]}, stateless=True
        ):
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "text-delta":
                if first_text is None and str(event.get("delta") or "").strip():
                    first_text = time.monotonic() - started
                text.append(event.get("delta") or "")
            elif kind == "tool-input-available":
                tools.append({"name": event.get("toolName"), "input": event.get("input")})
            elif kind == "tool-output-available" and tools:
                tools[-1]["output"] = event.get("output")
            elif kind == "response-metadata":
                for key, value in (event.get("usage") or {}).items():
                    if isinstance(value, (int, float)):
                        usage[key] = usage.get(key, 0) + value
            elif kind == "error":
                errors.append(event.get("errorText") or str(event))
    except Exception as exc:  # noqa: BLE001 - a provider failure is a result too
        errors.append(f"{type(exc).__name__}: {exc}")
    return {
        "first_text_s": first_text,
        "total_s": time.monotonic() - started,
        "answer": "".join(text),
        "tools": tools,
        "usage": usage,
        "errors": errors,
    }


def ground_truth() -> str:
    with connect_readonly(ensure_case_db()) as db:
        rows = db.execute(
            "SELECT b.product_name, b.cpm, b.ctr, b.in_view_rate, b.ecpc, b.vcpm, i.available_imps,"
            " i.inventory_risk FROM benchmarks b JOIN inventory i USING (product_id, vertical)"
            " WHERE b.vertical = 'QSR' AND i.geo = 'US' ORDER BY b.in_view_rate DESC"
        ).fetchall()
    lines = [
        f"{r['product_name']}: CPM ${r['cpm']:.0f}, CTR {r['ctr']:.4f}, in-view {r['in_view_rate']:.3f}, "
        f"eCPC ${r['ecpc']:.2f}, vCPM ${r['vcpm']:.2f}, US QSR available {r['available_imps']:,} "
        f"(confidence {r['inventory_risk']:.2f}); $15k buys {15000 / r['cpm'] * 1000:,.0f} imps"
        for r in rows
    ]
    return "\n".join(lines)


JUDGE_PROMPT = """You grade a media-planning assistant's answer against the true data.

Question: {question}

True QSR benchmarks (US inventory shown), ordered by in-view rate:
{truth}

Facts: Social Amplifier has BOTH the highest CTR and the highest in-view rate, and the lowest eCPC and vCPM.
Video Spotlight has the LOWEST CTR among the six, a mid in-view rate (below Social Amplifier and
Attention Builder) and is expensive per click and per viewable impression. So the data does NOT
favor video on either KPI.

Answer to grade:
<<<
{answer}
>>>

Return JSON only:
{{"verdict_correct": bool,       // the answer's conclusion about video matches the data (video is not the best choice on CTR or in-view)
 "wrong_claims": [str],          // each factually wrong statement about the data (empty if none)
 "invented_numbers": bool,       // states figures that contradict the true data above
 "compares_both_kpis": bool,     // discusses both CTR and in-view rate
 "asks_kpi_or_caveats": bool,    // asks which KPI matters, or clearly conditions the advice on it
 "commits_plan": bool}}          // presents a committed budget allocation / final plan"""


async def judge(answer: str, truth: str) -> dict:
    import httpx

    async with httpx.AsyncClient(timeout=120) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"},
            json={
                "model": JUDGE_MODEL,
                "response_format": {"type": "json_object"},
                "messages": [
                    {
                        "role": "user",
                        "content": JUDGE_PROMPT.format(question=QUESTION, truth=truth, answer=answer),
                    }
                ],
            },
        )
    response.raise_for_status()
    return json.loads(response.json()["choices"][0]["message"]["content"])


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=4)
    parser.add_argument("--only", default="")
    args = parser.parse_args()

    out_dir = HERE / "results" / datetime.now().strftime("%Y%m%d-%H%M%S")
    out_dir.mkdir(parents=True)
    truth = ground_truth()
    models = [m for m in MODELS if args.only.lower() in m[0].lower()]
    gate = asyncio.Semaphore(4)

    async def one(label, provider, model, effort, n):
        async with gate:
            result = await run_once(provider, model, effort)
            result["grade"] = await judge(result["answer"], truth) if result["answer"].strip() else None
            result.update(label=label, model=model, run=n)
            print(
                f"{label:24} run {n}: first text {result['first_text_s'] or float('nan'):5.2f}s "
                f"total {result['total_s']:5.1f}s tools {len(result['tools'])} "
                f"correct {None if not result['grade'] else result['grade']['verdict_correct']} "
                f"errors {len(result['errors'])}",
                flush=True,
            )
            return result

    results = await asyncio.gather(
        *(one(*m, n) for m in models for n in range(1, args.runs + 1))
    )
    (out_dir / "runs.json").write_text(json.dumps(results, indent=2, default=str))

    print(f"\n{'model':24} correct  invented  asks-KPI  commits  first-text p50  total p50  tools/run  in/out tokens")
    for label, *_ in models:
        rs = [r for r in results if r["label"] == label]
        graded = [r["grade"] for r in rs if r["grade"]]
        firsts = [r["first_text_s"] for r in rs if r["first_text_s"] is not None]
        count = lambda key: sum(bool(g.get(key)) for g in graded)  # noqa: E731
        print(
            f"{label:24} {count('verdict_correct')}/{len(rs)}      {count('invented_numbers')}/{len(rs)}       "
            f"{count('asks_kpi_or_caveats')}/{len(rs)}       {count('commits_plan')}/{len(rs)}      "
            f"{statistics.median(firsts) if firsts else float('nan'):6.2f}s        "
            f"{statistics.median(r['total_s'] for r in rs):6.1f}s     "
            f"{statistics.mean(len(r['tools']) for r in rs):4.1f}      "
            f"{statistics.mean(r['usage'].get('inputTokens', 0) for r in rs):7.0f}/"
            f"{statistics.mean(r['usage'].get('outputTokens', 0) for r in rs):.0f}"
        )
    print(f"\nresults: {out_dir}")


if __name__ == "__main__":
    asyncio.run(main())
