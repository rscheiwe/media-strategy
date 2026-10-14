"""How the app works, for the chat agent: a small, topic-indexed knowledge base.

The agent knows the data, the metrics and its tools, but not the workflow's design: asked "what
are the stages?", it reconstructed a plausible pipeline from the run_recommendation description
and got the order and several stages wrong. Questions about how the app works or why it decides
what it does now read the curated text in `chat_api/knowledge/*.md` instead.

A handful of short documents, so no embeddings or search: the topic index is in the tool
description and the prompt, and the agent reads a topic whole. Each result also states the
policies this server runs under, since the documents describe the defaults.
"""

from __future__ import annotations

from pathlib import Path

from vel.tools.registry import ToolSpec  # type: ignore

from chat_api.workflow.policy import inventory_policy, selection_policy

KNOWLEDGE_DIR = Path(__file__).resolve().parent.parent / "knowledge"

TOPICS: dict[str, str] = {
    "overview": "what the app does, the model-vs-code principle, what the assistant can do, the case data",
    "workflow": "the 8 stages of the recommendation workflow in order, what each does, a worked example",
    "benchmarks": "how CTR and in-view benchmarks are computed (ratio of sums) and what they ignore",
    "inventory": "forecast, confidence, usable inventory = forecast × confidence, why, alternatives tested",
    "selection": "how the plan is chosen: ranking, single product first, bundles, fallback, why, limits",
    "card": "the recommendation card's sections, rejection reasons, tradeoffs, what the model writes",
    "glossary": "definitions of the planning terms",
}


def topic_index() -> str:
    return "\n".join(f"- {name}: {about}" for name, about in TOPICS.items())


def read_topic(topic: str) -> str:
    return (KNOWLEDGE_DIR / f"{topic}.md").read_text()


def _active_policies() -> str:
    inv, sel = inventory_policy(), selection_policy()
    return (
        f"Usable inventory = {inv.rule()}; a product fits alone at {inv.min_absorption:.0%} coverage. "
        f"Selection: {sel.mode}, up to {sel.max_lines} products, ${sel.min_line_spend:,.0f} minimum per line."
    )


def _read_knowledge(inp: dict, ctx: dict) -> dict:
    topic = str(inp.get("topic") or "").strip().lower()
    if topic not in TOPICS:
        return {"error": f"Unknown topic {topic!r}. Topics: {', '.join(TOPICS)}."}
    try:
        content = read_topic(topic)
    except OSError as exc:
        return {"error": f"Knowledge unavailable: {exc}"}
    return {
        "topic": topic,
        "content": content,
        "active_policies": _active_policies(),
        "note": "Worked examples use the default policies. For this chat's brief, use the recommendation and the step tools.",
    }


READ_KNOWLEDGE = ToolSpec(
    name="read_knowledge",
    description=(
        "How this app works and why it decides what it does: curated documentation of the "
        "recommendation workflow and its rules. Use it for questions about the workflow's stages, "
        "the benchmark method, the inventory rule, how a plan or bundle is chosen, why a product "
        "was picked or rejected in general, what a card section or tradeoff means, or a planning "
        "term. Read the topic and answer from it; never describe the app's design from memory. "
        "Topics:\n" + topic_index()
    ),
    input_schema={
        "type": "object",
        "properties": {"topic": {"type": "string", "enum": list(TOPICS)}},
        "required": ["topic"],
    },
    output_schema={"type": "object"},
    handler=_read_knowledge,
    parallel_safe=True,
)
