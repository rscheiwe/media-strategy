from __future__ import annotations

from collections import defaultdict
from typing import Callable

from fastapi import APIRouter, Depends, Query

_TOKEN_FIELDS = ("input_tokens", "output_tokens", "reasoning_tokens", "cached_input_tokens", "total_tokens")


def _camel(name: str) -> str:
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest)


def create_usage_router(*, storage, require_auth: Callable) -> APIRouter:
    router = APIRouter(prefix="/app/usage", tags=["usage"])

    @router.get("")
    def usage_summary(
        days: int = Query(default=30, ge=1, le=365),
        user: dict = Depends(require_auth),
    ) -> dict:
        events = storage.usage.list_recent(user["id"], days=days)

        def blank() -> dict:
            return {"turns": 0, **{_camel(name): 0 for name in _TOKEN_FIELDS}}

        totals = blank()
        by_day: dict[str, dict] = defaultdict(blank)
        by_model: dict[str, dict] = defaultdict(blank)
        for event in events:
            day = event.created_at.date().isoformat()
            model = event.model or "unknown"
            for bucket in (totals, by_day[day], by_model[model]):
                bucket["turns"] += 1
                for name in _TOKEN_FIELDS:
                    bucket[_camel(name)] += getattr(event, name)
        return {
            "days": days,
            "totals": totals,
            "byDay": [{"date": day, **values} for day, values in sorted(by_day.items())],
            "byModel": sorted(
                ({"model": model, **values} for model, values in by_model.items()),
                key=lambda item: item["totalTokens"],
                reverse=True,
            ),
        }

    return router
