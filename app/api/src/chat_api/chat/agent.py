"""
chat.agent — Vel Agent factory for the chat agent.

What it sets up:
- Provider from env: OpenAI by default, or OpenRouter / Ollama via their OpenAI-compatible endpoints
- Optional native-reasoning model swap (OpenRouter) when thinking is on
- Optional Langfuse observability

One agent is created per HTTP request and discarded after streaming ends.
"""

from __future__ import annotations

import json
import os
from typing import Any

from .agents import (
    DEFAULT_OLLAMA_BASE_URL,
    DEFAULT_OPENROUTER_BASE_URL,
    _base_model,
    agent_tools,
    chat_provider,
    get_chat_agent_config,
    reasoning_model,
    with_current_date,
    with_custom_instructions,
    with_page_context,
)


def _sse_data(obj: dict[str, Any]) -> str:
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n"


def _env_bool(name: str, default: bool) -> bool:
    import os

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() not in {"0", "false", "no", "off", ""}


def _env_float(name: str, default: float) -> float:
    import os

    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def build_observability_config(
    agent_config,
    context: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    import os

    public_key = os.getenv("LANGFUSE_PUBLIC_KEY", "").strip()
    secret_key = os.getenv("LANGFUSE_SECRET_KEY", "").strip()
    default_enabled = bool(public_key and secret_key)
    enabled = _env_bool("LANGFUSE_ENABLED", default_enabled)
    if not (enabled and public_key and secret_key):
        return None

    context = context or {}
    metadata = {
        "agent_uuid": agent_config.uuid,
        "agent_label": agent_config.label,
        **(context.get("metadata") or {}),
    }
    tags = list(
        dict.fromkeys(
            [
                "chat",
                agent_config.id,
                *(context.get("tags") or []),
            ]
        )
    )

    return {
        "provider": "langfuse",
        "enabled": True,
        "public_key": public_key,
        "secret_key": secret_key,
        "host": os.getenv("LANGFUSE_HOST", "").strip() or os.getenv("LANGFUSE_BASE_URL", "").strip() or None,
        "user_id": context.get("user_id"),
        "session_id": context.get("session_id"),
        "trace_name": context.get("trace_name") or agent_config.id,
        "tags": tags,
        "metadata": metadata,
        "sample_rate": _env_float("LANGFUSE_SAMPLE_RATE", 1.0),
        "capture_input": _env_bool("LANGFUSE_CAPTURE_INPUT", True),
        "capture_output": _env_bool("LANGFUSE_CAPTURE_OUTPUT", True),
        "capture_tool_io": _env_bool("LANGFUSE_CAPTURE_TOOL_IO", True),
    }



def _reasoning_provider_preference() -> dict | None:
    """OpenRouter provider routing preference, or None to let it choose.

    `APP_REASONING_PROVIDER_ORDER` is a comma-separated preference list, e.g.
    "Novita,Baidu". Set it empty to disable steering entirely.
    """
    import os

    raw = os.getenv("APP_REASONING_PROVIDER_ORDER")
    if raw is None:
        raw = "Novita"
    names = [name.strip() for name in raw.split(",") if name.strip()]
    return {"order": names} if names else None


def workflow_model_config(api_key: str | None = None) -> dict[str, Any]:
    """The model config for the workflow's router and explain step (see WORKFLOW_MODELS)."""
    import os

    from .agents import WORKFLOW_MODELS, chat_provider

    config = model_config(api_key)
    model = os.getenv("APP_WORKFLOW_MODEL") or WORKFLOW_MODELS.get(chat_provider())
    return {**config, "model": model} if model else config


def model_config(api_key: str | None = None) -> dict[str, Any]:
    """The vel model config for the chat agent, from the environment.

    - APP_CHAT_PROVIDER=openai (default): OpenAI; the key comes from `api_key` or
      OPENAI_API_KEY; APP_CHAT_BASE_URL optionally points at another
      OpenAI-compatible endpoint.
    - APP_CHAT_PROVIDER=openrouter: OpenRouter's OpenAI-compatible endpoint
      (APP_CHAT_BASE_URL, default https://openrouter.ai/api/v1) with
      OPENROUTER_API_KEY; the model is an OpenRouter id, e.g. openai/gpt-5.4.
    - APP_CHAT_PROVIDER=ollama: Ollama's OpenAI-compatible endpoint
      (APP_CHAT_BASE_URL, default http://ollama:11434/v1) through vel's openai
      provider; no key needed.

    The key may be absent here; create_chat_agent requires it for OpenAI and OpenRouter.
    """
    import os

    base = _base_model()
    base_url = (os.getenv("APP_CHAT_BASE_URL") or "").strip()
    if base["provider"] == "ollama":
        return {
            "provider": "openai",
            "model": base["model"],
            "base_url": base_url or DEFAULT_OLLAMA_BASE_URL,
            "api_key": "ollama",
        }
    if base["provider"] == "openrouter":
        # Never the incoming key: the router passes OPENAI_API_KEY, which OpenRouter rejects.
        config = {
            "provider": "openai",
            "model": base["model"],
            "base_url": base_url or DEFAULT_OPENROUTER_BASE_URL,
        }
        key = (os.getenv("OPENROUTER_API_KEY") or "").strip()
        if key:
            config["api_key"] = key
        return config
    config: dict[str, Any] = {"provider": "openai", "model": base["model"]}
    incoming = (api_key or "").strip()
    if incoming.lower() in {"none", "null", "undefined"}:
        incoming = ""
    key = incoming or (os.getenv("OPENAI_API_KEY") or "").strip()
    if key:
        config["api_key"] = key
    if base_url:
        config["base_url"] = base_url
    return config


def create_chat_agent(
    api_key: str | None = None,
    agent_uuid: str | None = None,
    user_id: str | None = None,
    observability_context: dict[str, Any] | None = None,
    reasoning_effort: str | None = None,
    custom_instructions: str | None = None,
    page_context: dict[str, Any] | None = None,
    timezone: str | None = None,
):  # -> Agent
    """
    Create a one-shot chat Agent.

    Args:
        api_key: Provider API key. Falls back to OPENAI_API_KEY if empty (unused for Ollama).
        reasoning_effort: When set and `APP_REASONING_MODEL` is configured, swaps the chat
            model for the natively-reasoning one and asks it for this much thinking. When the
            reasoning model is unconfigured this is ignored, and the caller is expected to fall
            back to reflection.

    Returns:
        A configured Vel Agent ready for run_stream().

    Raises:
        ValueError: OpenAI is selected and no key is available. The chat router turns this
            into an in-stream error.
    """
    from vel import Agent  # type: ignore

    config = get_chat_agent_config(agent_uuid)

    # Thinking swaps the whole model rather than layering a second one on top —
    # see `agents.reasoning_model` for why. The swapped model brings its own
    # credentials and base_url, so the ambient model config below is skipped.
    reasoning = reasoning_model() if reasoning_effort else None

    if reasoning is not None:
        resolved: dict[str, Any] = dict(reasoning)
    else:
        resolved = model_config(api_key)
        if "api_key" not in resolved:
            key_name = "OPENROUTER_API_KEY" if chat_provider() == "openrouter" else "OPENAI_API_KEY"
            raise ValueError(f"{key_name} is not set and no api_key was provided.")

    policies: dict[str, Any] = dict(config.policies)
    generation_config: dict[str, Any] = dict(config.generation_config)

    if reasoning is None:
        provider = chat_provider()
        # APP_CHAT_REASONING_EFFORT (e.g. minimal/low) for reasoning models. The two
        # endpoints spell it differently; vel forwards either key verbatim.
        effort = (os.getenv("APP_CHAT_REASONING_EFFORT") or "").strip().lower()
        if effort:
            if provider == "openrouter":
                generation_config["reasoning"] = {"effort": effort}
            else:
                generation_config["reasoning_effort"] = effort
        if provider == "openrouter":
            # OpenRouter spreads one model across backends whose first-token times
            # differ by up to 10x; prefer the fastest.
            generation_config.setdefault("provider", {"sort": "latency"})

    if reasoning is not None:
        # OpenRouter takes `reasoning: {effort}`; Vel forwards the key verbatim.
        # Efforts above "high" are UI-side labels with no wire equivalent, so they clamp.
        effort = reasoning_effort if reasoning_effort in {"low", "medium", "high"} else "high"
        generation_config["reasoning"] = {"effort": effort}
        # OpenRouter provider routing preference (see APP_REASONING_PROVIDER_ORDER).
        preference = _reasoning_provider_preference()
        if preference:
            generation_config.setdefault("provider", preference)

    return Agent(
        id=config.id,
        model=resolved,
        tools=agent_tools(config.workflow_trigger),
        # `datasets`: rows get_case_data fetched this turn, by id, for run_python_analysis.
        # One agent per request, so the store lasts exactly one turn.
        tool_context={"user_id": user_id, "datasets": {}},
        system_prompt=with_page_context(
            with_custom_instructions(with_current_date(config.system_prompt, tz=timezone), custom_instructions),
            page_context,
        ),
        policies=policies,
        generation_config=generation_config,
        observability=build_observability_config(config, observability_context),
    )
