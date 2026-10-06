from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class ChatMessage:
    id: str
    chat_id: str
    role: str
    content: str
    parts_json: list[dict[str, Any]] = field(default_factory=list)
    metadata_json: dict[str, Any] = field(default_factory=dict)
    display_order: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class Chat:
    id: str
    user_id: str
    title: str
    agent_uuid: str | None = None
    is_archived: bool = False
    pinned_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
