from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..db.base import DatabaseClient
from ..models.user_settings import UserSettings
from ._rows import as_dict, now_or


class UserSettingsRepository:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    def get(self, user_id: str) -> UserSettings | None:
        row = self._db.execute_one("SELECT * FROM user_settings WHERE user_id = %s", (user_id,))
        return self._row_to_model(row) if row else None

    def get_or_default(self, user_id: str) -> UserSettings:
        return self.get(user_id) or UserSettings(user_id=user_id)

    def upsert(self, settings: UserSettings) -> UserSettings:
        # Read-then-write (not ON CONFLICT DO UPDATE), as in UserProfileRepository: the in-memory
        # test DB ignores an ON CONFLICT tail and would append duplicate rows.
        settings.updated_at = datetime.now(UTC)
        values = (
            settings.theme,
            settings.default_thinking,
            settings.thinking_effort,
            settings.default_agent_uuid,
            settings.custom_instructions,
            settings.send_on_enter,
            json.dumps(settings.extra or {}),
            settings.updated_at.isoformat(),
        )
        if self.get(settings.user_id) is not None:
            self._db.execute_write(
                "UPDATE user_settings SET theme = %s, default_thinking = %s, thinking_effort = %s,"
                " default_agent_uuid = %s, custom_instructions = %s, send_on_enter = %s, extra = %s,"
                " updated_at = %s WHERE user_id = %s",
                (*values, settings.user_id),
            )
        else:
            self._db.execute_write(
                "INSERT INTO user_settings"
                " (theme, default_thinking, thinking_effort, default_agent_uuid, custom_instructions,"
                " send_on_enter, extra, updated_at, user_id, created_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (*values, settings.user_id, settings.created_at.isoformat()),
            )
        return self.get(settings.user_id) or settings

    @staticmethod
    def _row_to_model(row: dict[str, Any]) -> UserSettings:
        return UserSettings(
            user_id=str(row["user_id"]),
            theme=row.get("theme") or "system",
            default_thinking=bool(row.get("default_thinking", False)),
            thinking_effort=row.get("thinking_effort") or "high",
            default_agent_uuid=row.get("default_agent_uuid"),
            custom_instructions=row.get("custom_instructions") or "",
            send_on_enter=bool(row.get("send_on_enter", True)),
            extra=as_dict(row.get("extra")),
            created_at=now_or(row.get("created_at")),
            updated_at=now_or(row.get("updated_at")),
        )
