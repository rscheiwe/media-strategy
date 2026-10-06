from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

THEMES = ("system", "light", "dark")
THINKING_EFFORTS = ("low", "medium", "high", "extra", "max")
CUSTOM_INSTRUCTIONS_MAX = 4000


@dataclass
class UserSettings:
    """Per-user preferences (user_settings table). A user with no row reads as these defaults."""

    user_id: str
    theme: str = "system"
    default_thinking: bool = False
    thinking_effort: str = "high"
    default_agent_uuid: str | None = None
    custom_instructions: str = ""
    send_on_enter: bool = True
    extra: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
