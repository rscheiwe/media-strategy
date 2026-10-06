from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ..db.base import DatabaseClient
from ..models.feedback import MessageFeedback
from ._rows import now_or


class MessageFeedbackRepository:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    def get(self, message_id: str, user_id: str) -> MessageFeedback | None:
        row = self._db.execute_one(
            "SELECT * FROM message_feedback WHERE message_id = %s AND user_id = %s",
            (message_id, user_id),
        )
        return self._row_to_model(row) if row else None

    def list_for_chat(self, chat_id: str, user_id: str) -> list[MessageFeedback]:
        rows = self._db.execute(
            "SELECT * FROM message_feedback WHERE chat_id = %s AND user_id = %s",
            (chat_id, user_id),
        )
        return [self._row_to_model(row) for row in rows]

    def upsert(
        self,
        *,
        message_id: str,
        chat_id: str,
        user_id: str,
        rating: int,
        comment: str | None,
    ) -> MessageFeedback:
        # Read-then-write for in-memory test DB parity (see UserSettingsRepository.upsert).
        now = datetime.now(UTC).isoformat()
        if self.get(message_id, user_id) is not None:
            self._db.execute_write(
                "UPDATE message_feedback SET rating = %s, comment = %s, updated_at = %s"
                " WHERE message_id = %s AND user_id = %s",
                (rating, comment, now, message_id, user_id),
            )
        else:
            self._db.execute_write(
                "INSERT INTO message_feedback"
                " (id, message_id, chat_id, user_id, rating, comment, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (str(uuid.uuid4()), message_id, chat_id, user_id, rating, comment, now, now),
            )
        return self.get(message_id, user_id)  # type: ignore[return-value]

    def delete(self, message_id: str, user_id: str) -> None:
        self._db.execute_write(
            "DELETE FROM message_feedback WHERE message_id = %s AND user_id = %s",
            (message_id, user_id),
        )

    @staticmethod
    def _row_to_model(row: dict[str, Any]) -> MessageFeedback:
        return MessageFeedback(
            id=str(row["id"]),
            message_id=str(row["message_id"]),
            chat_id=str(row["chat_id"]),
            user_id=str(row["user_id"]),
            rating=int(row["rating"]),
            comment=row.get("comment"),
            created_at=now_or(row.get("created_at")),
            updated_at=now_or(row.get("updated_at")),
        )
