"""Row-coercion helpers shared by repositories (rows come back as dicts from psycopg2, or as the
raw Python values the in-memory test DB stored)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any


def as_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def now_or(value: Any) -> datetime:
    return as_dt(value) or datetime.now(UTC)


def as_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}
