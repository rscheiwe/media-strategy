from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, Field


class WorkflowState(BaseModel):
    """Per-chat state of the recommendation workflow (see schema.sql: chat_workflow_state).

    slots: {name: {"value": ..., "source": "llm"|"router"|"form", "turn": int}}
    last_run: {"run_id", "idem_key", "policy_version", "summary", "artifact_id"}
    """

    chat_id: str
    slots: dict[str, dict[str, Any]] = Field(default_factory=dict)
    pending_request: bool = False
    asked_for: list[str] = Field(default_factory=list)
    active_form_id: str | None = None
    running_since: datetime | None = None
    last_run: dict[str, Any] | None = None
    updated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    def slot_values(self) -> dict[str, Any]:
        return {name: entry.get("value") for name, entry in self.slots.items()}
