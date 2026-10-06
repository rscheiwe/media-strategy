from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from ..db.base import DatabaseClient
from ..models.usage import UsageEvent
from ._rows import now_or

# Upper bound on rows read for a summary. Aggregation is done in Python so the same query runs on
# the in-memory test DB; at chat-app volumes this is a few KB per user per month.
_SUMMARY_ROW_LIMIT = 5000


class UsageEventRepository:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    def record(self, event: UsageEvent) -> None:
        self._db.execute_write(
            "INSERT INTO usage_events"
            " (id, user_id, chat_id, message_id, agent_uuid, model, thinking, status, input_tokens,"
            " output_tokens, reasoning_tokens, cached_input_tokens, total_tokens, steps, duration_ms,"
            " created_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (
                event.id or str(uuid.uuid4()),
                event.user_id,
                event.chat_id,
                event.message_id,
                event.agent_uuid,
                event.model,
                event.thinking,
                event.status,
                event.input_tokens,
                event.output_tokens,
                event.reasoning_tokens,
                event.cached_input_tokens,
                event.total_tokens,
                event.steps,
                event.duration_ms,
                event.created_at.isoformat(),
            ),
        )

    def list_recent(self, user_id: str, *, days: int) -> list[UsageEvent]:
        rows = self._db.execute(
            "SELECT * FROM usage_events WHERE user_id = %s ORDER BY created_at DESC LIMIT %s",
            (user_id, _SUMMARY_ROW_LIMIT),
        )
        cutoff = datetime.now(UTC) - timedelta(days=days)
        events = [self._row_to_model(row) for row in rows]
        return [event for event in events if event.created_at >= cutoff]

    @staticmethod
    def _row_to_model(row: dict[str, Any]) -> UsageEvent:
        def _int(key: str) -> int:
            return int(row.get(key) or 0)

        return UsageEvent(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            chat_id=str(row["chat_id"]) if row.get("chat_id") else None,
            message_id=str(row["message_id"]) if row.get("message_id") else None,
            agent_uuid=row.get("agent_uuid"),
            model=row.get("model"),
            thinking=bool(row.get("thinking", False)),
            status=row.get("status") or "completed",
            input_tokens=_int("input_tokens"),
            output_tokens=_int("output_tokens"),
            reasoning_tokens=_int("reasoning_tokens"),
            cached_input_tokens=_int("cached_input_tokens"),
            total_tokens=_int("total_tokens"),
            steps=_int("steps"),
            duration_ms=row.get("duration_ms"),
            created_at=now_or(row.get("created_at")),
        )
