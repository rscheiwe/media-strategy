from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class MessageFeedback:
    id: str
    message_id: str
    chat_id: str
    user_id: str
    rating: int  # -1 or 1
    comment: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
