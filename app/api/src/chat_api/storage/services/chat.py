from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ..models.chat import Chat, ChatMessage
from ..repositories.chat import ChatRepository


def title_from_message(content: str) -> str:
    title = " ".join((content or "").strip().split())
    if not title:
        return "New chat"
    return title[:64].rstrip() + ("..." if len(title) > 64 else "")


class ChatService:
    """Chat CRUD, scoped to the owning user.

    Ownership is checked here as well as by RLS: in dev the server may connect as the table
    owner (RLS bypassed), so this check is what keeps one user's chats from another's there.
    """

    def __init__(self, repo: ChatRepository) -> None:
        self._repo = repo

    def create_chat(
        self,
        *,
        user_id: str,
        title: str,
        agent_uuid: str | None = None,
    ) -> Chat:
        now = datetime.now(UTC)
        chat = Chat(
            id=self._repo.new_chat_id(),
            user_id=user_id,
            title=title_from_message(title),
            agent_uuid=agent_uuid,
            created_at=now,
            updated_at=now,
        )
        self._repo.create(chat)
        return chat

    def get_chat(self, *, chat_id: str, user_id: str) -> Chat | None:
        chat = self._repo.get(chat_id)
        if chat is None or chat.user_id != user_id:
            return None
        return chat

    def list_chats(
        self,
        *,
        user_id: str,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Chat]:
        return self._repo.list_for_user(user_id, limit=limit, offset=offset)

    def update_chat(
        self,
        *,
        chat_id: str,
        user_id: str,
        title: str | None = None,
        is_archived: bool | None = None,
        pinned: bool | None = None,
    ) -> Chat | None:
        chat = self.get_chat(chat_id=chat_id, user_id=user_id)
        if chat is None:
            return None
        if title is not None:
            title = " ".join(title.strip().split())[:200] or chat.title
        return self._repo.update(chat_id, title=title, is_archived=is_archived, pinned=pinned)

    def get_message(self, *, chat_id: str, message_id: str, user_id: str) -> ChatMessage | None:
        messages = self.list_messages(chat_id=chat_id, user_id=user_id) or []
        return next((message for message in messages if message.id == message_id), None)

    def list_messages(self, *, chat_id: str, user_id: str) -> list[ChatMessage] | None:
        chat = self.get_chat(chat_id=chat_id, user_id=user_id)
        if chat is None:
            return None
        return self._repo.list_messages(chat_id)

    def append_message(
        self,
        *,
        chat_id: str,
        user_id: str,
        role: str,
        content: str,
        parts_json: list[dict[str, Any]] | None = None,
        metadata_json: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> ChatMessage | None:
        chat = self.get_chat(chat_id=chat_id, user_id=user_id)
        if chat is None:
            return None
        message = ChatMessage(
            # Callers may pre-assign the id so a streamed message's client id matches its row.
            id=message_id or self._repo.new_message_id(),
            chat_id=chat_id,
            role=role,
            content=content or "",
            parts_json=parts_json or [{"type": "text", "text": content or ""}],
            metadata_json=metadata_json or {},
            display_order=self._repo.next_display_order(chat_id),
            created_at=datetime.now(UTC),
        )
        self._repo.append_message(message)
        return message
