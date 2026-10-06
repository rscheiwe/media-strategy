from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class UsageEvent:
    """Tokens for one assistant turn, summed across the agent's steps."""

    id: str
    user_id: str
    chat_id: str | None = None
    message_id: str | None = None
    agent_uuid: str | None = None
    model: str | None = None
    thinking: bool = False
    status: str = "completed"  # completed | stopped | error
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    steps: int = 0
    duration_ms: int | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
