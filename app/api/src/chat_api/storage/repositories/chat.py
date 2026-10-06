from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

from ..db.base import DatabaseClient
from ..models.chat import Chat, ChatMessage


def _dt(value):
    if value is None:
        return None
    return datetime.fromisoformat(value) if isinstance(value, str) else value


def _json_list(value) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return value
    if not value:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return []


def _json_dict(value) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


class ChatRepository:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    def create(self, chat: Chat) -> None:
        self._db.execute_write(
            "INSERT INTO chats "
            "(id, user_id, title, agent_uuid, is_archived, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s)",
            (
                chat.id,
                chat.user_id,
                chat.title,
                chat.agent_uuid,
                chat.is_archived,
                chat.created_at.isoformat(),
                chat.updated_at.isoformat(),
            ),
        )

    def get(self, chat_id: str) -> Chat | None:
        row = self._db.execute_one("SELECT * FROM chats WHERE id = %s", (chat_id,))
        return self._row_to_chat(row) if row else None

    def list_for_user(
        self,
        user_id: str,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Chat]:
        # Pinned chats first (most recently pinned on top), then everything else by recency.
        sql = (
            "SELECT * FROM chats WHERE user_id = %s AND is_archived = %s"
            " ORDER BY pinned_at DESC NULLS LAST, updated_at DESC"
        )
        params: list[Any] = [user_id, False]
        if limit is not None:
            sql += " LIMIT %s"
            params.append(limit)
        if offset:
            sql += " OFFSET %s"
            params.append(offset)
        rows = self._db.execute(sql, tuple(params))
        return [self._row_to_chat(row) for row in rows]

    def update(
        self,
        chat_id: str,
        *,
        title: str | None = None,
        is_archived: bool | None = None,
        pinned: bool | None = None,
    ) -> Chat | None:
        chat = self.get(chat_id)
        if chat is None:
            return None
        next_title = title if title is not None else chat.title
        next_archived = is_archived if is_archived is not None else chat.is_archived
        if pinned is None:
            next_pinned_at = chat.pinned_at
        elif pinned:
            next_pinned_at = chat.pinned_at or datetime.now(UTC)
        else:
            next_pinned_at = None
        # Pinning is not activity: it must not bump the chat up the recency order.
        content_changed = next_title != chat.title or next_archived != chat.is_archived
        updated_at = datetime.now(UTC) if content_changed else chat.updated_at
        self._db.execute_write(
            "UPDATE chats SET title = %s, is_archived = %s, pinned_at = %s, updated_at = %s WHERE id = %s",
            (
                next_title,
                next_archived,
                next_pinned_at.isoformat() if next_pinned_at else None,
                updated_at.isoformat(),
                chat_id,
            ),
        )
        return self.get(chat_id)

    def touch(self, chat_id: str) -> None:
        self._db.execute_write(
            "UPDATE chats SET updated_at = %s WHERE id = %s",
            (datetime.now(UTC).isoformat(), chat_id),
        )

    def append_message(self, message: ChatMessage) -> None:
        self._db.execute_write(
            "INSERT INTO chat_messages "
            "(id, chat_id, role, content, parts_json, metadata_json, display_order, created_at) "
            "VALUES (%s, %s, %s, %s, %s::jsonb, %s::jsonb, %s, %s)",
            (
                message.id,
                message.chat_id,
                message.role,
                message.content,
                json.dumps(message.parts_json),
                json.dumps(message.metadata_json),
                message.display_order,
                message.created_at.isoformat(),
            ),
        )
        self.touch(message.chat_id)

    def list_messages(self, chat_id: str) -> list[ChatMessage]:
        rows = self._db.execute("SELECT * FROM chat_messages WHERE chat_id = %s", (chat_id,))
        messages = [self._row_to_message(row) for row in rows]
        return sorted(messages, key=lambda item: (item.display_order, item.created_at))

    def next_display_order(self, chat_id: str) -> int:
        messages = self.list_messages(chat_id)
        if not messages:
            return 0
        return max(message.display_order for message in messages) + 100

    @staticmethod
    def new_chat_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def new_message_id() -> str:
        return str(uuid.uuid4())

    @staticmethod
    def _row_to_chat(row: dict) -> Chat:
        return Chat(
            id=str(row["id"]),
            user_id=str(row["user_id"]),
            title=row["title"],
            agent_uuid=row.get("agent_uuid"),
            is_archived=bool(row.get("is_archived", False)),
            pinned_at=_dt(row.get("pinned_at")),
            created_at=_dt(row.get("created_at")) or datetime.now(UTC),
            updated_at=_dt(row.get("updated_at")) or datetime.now(UTC),
        )

    @staticmethod
    def _row_to_message(row: dict) -> ChatMessage:
        return ChatMessage(
            id=str(row["id"]),
            chat_id=str(row["chat_id"]),
            role=row["role"],
            content=row.get("content") or "",
            parts_json=_json_list(row.get("parts_json")),
            metadata_json=_json_dict(row.get("metadata_json")),
            display_order=int(row.get("display_order") or 0),
            created_at=_dt(row.get("created_at")) or datetime.now(UTC),
        )
