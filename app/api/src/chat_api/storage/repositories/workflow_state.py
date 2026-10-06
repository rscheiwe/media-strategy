from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ..db.base import DatabaseClient
from ..models.workflow_state import WorkflowState
from ._rows import as_dict, now_or


class WorkflowStateRepository:
    def __init__(self, db: DatabaseClient) -> None:
        self._db = db

    def get(self, chat_id: str) -> WorkflowState:
        row = self._db.execute_one("SELECT * FROM chat_workflow_state WHERE chat_id = %s", (chat_id,))
        return self._row_to_model(row) if row else WorkflowState(chat_id=chat_id)

    def save(self, state: WorkflowState) -> WorkflowState:
        # Read-then-write, as in UserSettingsRepository (the in-memory test DB ignores ON CONFLICT).
        state.updated_at = datetime.now(UTC)
        values = (
            json.dumps(state.slots),
            int(state.pending_request),
            json.dumps(state.asked_for),
            state.active_form_id,
            state.running_since.isoformat() if state.running_since else None,
            json.dumps(state.last_run) if state.last_run is not None else None,
            state.updated_at.isoformat(),
        )
        exists = self._db.execute_one("SELECT chat_id FROM chat_workflow_state WHERE chat_id = %s", (state.chat_id,))
        if exists:
            self._db.execute_write(
                "UPDATE chat_workflow_state SET slots_json = %s, pending_request = %s, asked_for_json = %s,"
                " active_form_id = %s, running_since = %s, last_run_json = %s, updated_at = %s WHERE chat_id = %s",
                (*values, state.chat_id),
            )
        else:
            self._db.execute_write(
                "INSERT INTO chat_workflow_state (slots_json, pending_request, asked_for_json, active_form_id,"
                " running_since, last_run_json, updated_at, chat_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (*values, state.chat_id),
            )
        return self.get(state.chat_id)

    @staticmethod
    def _row_to_model(row: dict[str, Any]) -> WorkflowState:
        last_run = row.get("last_run_json")
        return WorkflowState(
            chat_id=str(row["chat_id"]),
            slots=as_dict(row.get("slots_json")),
            pending_request=bool(row.get("pending_request")),
            asked_for=json.loads(row.get("asked_for_json") or "[]"),
            active_form_id=row.get("active_form_id"),
            running_since=now_or(row["running_since"]) if row.get("running_since") else None,
            last_run=json.loads(last_run) if last_run else None,
            updated_at=now_or(row.get("updated_at")),
        )
