"""Small OpenRouter Decisions API client for the Jev routing experiment."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any

import httpx


ENDPOINT = os.getenv("JEV_ENDPOINT", "https://openrouter.ai/api/alpha/decisions")
MODEL = os.getenv("JEV_MODEL", "typesafe/jev-1.13")


@dataclass
class JevResult:
    answers: dict[str, Any]
    usage: dict[str, Any]
    model: str | None
    request_id: str | None
    latency_s: float


def decide(state: dict[str, Any], questions: dict[str, Any]) -> JevResult:
    key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    payload = {"model": MODEL, "state": state, "questions": questions}
    started = time.perf_counter()
    with httpx.Client(timeout=30.0) as client:
        response = client.post(
            ENDPOINT,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json=payload,
        )
    elapsed = time.perf_counter() - started
    if response.status_code >= 400:
        detail = response.text[:500]
        raise RuntimeError(f"Jev HTTP {response.status_code}: {detail}")
    body = response.json()
    return JevResult(
        answers=body.get("answers") or {},
        usage=body.get("usage") or {},
        model=body.get("model"),
        request_id=body.get("id"),
        latency_s=elapsed,
    )

