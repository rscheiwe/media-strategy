"""Example chats every user starts with, so a reviewer sees the app's main paths straight away.

`example_chats.json` holds real conversations exported from the app (chats, messages and
workflow state). On a user's first chat listing, each gets a copy under ids derived from the
user's id, and a flag in their settings records it: a deleted example stays deleted. On with
`APP_EXAMPLE_CHATS=1` (the Docker image sets it).

Export (from app/api, against the chat.db that holds the chats):
    APP_DB_PATH=./data/chat.db uv run python -m chat_api.storage.examples <chat_id> ...
"""

from __future__ import annotations

import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

EXAMPLES_PATH = Path(__file__).resolve().parent / "example_chats.json"
SEEDED_FLAG = "example_chats_seeded"
_NAMESPACE = uuid.UUID("6f1d9a52-3c47-4e0b-9a8d-2b5e7c4f1a90")

CHAT_COLUMNS = ("id", "title", "agent_uuid", "is_archived", "pinned_at", "created_at", "updated_at")
MESSAGE_COLUMNS = ("id", "role", "content", "parts_json", "metadata_json", "display_order", "created_at")
STATE_COLUMNS = ("slots_json", "pending_request", "asked_for_json", "active_form_id", "last_run_json", "updated_at")


def enabled() -> bool:
    return (os.getenv("APP_EXAMPLE_CHATS") or "").strip().lower() in ("1", "true", "yes", "on")


def _copy_id(user_id: str, original: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{user_id}:{original}"))


def load_examples(path: Path = EXAMPLES_PATH) -> list[dict[str, Any]]:
    return json.loads(path.read_text()) if path.exists() else []


def seed_examples(storage, user_id: str, examples: list[dict[str, Any]] | None = None) -> int:
    """Give the user their copy of the example chats, once. Returns how many were added."""
    settings = storage.user_settings.get_or_default(user_id)
    if (settings.extra or {}).get(SEEDED_FLAG):
        return 0
    examples = load_examples() if examples is None else examples
    if not examples:
        return 0
    db, added = storage.db, 0
    with db.transaction():
        for example in examples:
            chat = example["chat"]
            chat_id = _copy_id(user_id, chat["id"])
            # Skip it if they already have it: their copy, or the original (whoever exported it).
            if db.execute_one("SELECT id FROM chats WHERE id IN (%s, %s) AND user_id = %s", (chat_id, chat["id"], user_id)):
                continue
            db.execute_write(
                f"INSERT INTO chats (user_id, {', '.join(CHAT_COLUMNS)}) VALUES (%s{', %s' * len(CHAT_COLUMNS)})",
                (user_id, chat_id, *(chat[c] for c in CHAT_COLUMNS[1:])),
            )
            for message in example["messages"]:
                db.execute_write(
                    f"INSERT INTO chat_messages (chat_id, {', '.join(MESSAGE_COLUMNS)}) VALUES (%s{', %s' * len(MESSAGE_COLUMNS)})",
                    (chat_id, _copy_id(user_id, message["id"]), *(message[c] for c in MESSAGE_COLUMNS[1:])),
                )
            if state := example.get("workflow_state"):
                db.execute_write(
                    f"INSERT INTO chat_workflow_state (chat_id, {', '.join(STATE_COLUMNS)}) VALUES (%s{', %s' * len(STATE_COLUMNS)})",
                    (chat_id, *(state[c] for c in STATE_COLUMNS)),
                )
            added += 1
    settings.extra = {**(settings.extra or {}), SEEDED_FLAG: True}
    storage.user_settings.upsert(settings)
    return added


def export_examples(db, chat_ids: list[str]) -> list[dict[str, Any]]:
    out = []
    for chat_id in chat_ids:
        chat = db.execute_one(f"SELECT {', '.join(CHAT_COLUMNS)} FROM chats WHERE id = %s", (chat_id,))
        if chat is None:
            raise SystemExit(f"no chat {chat_id}")
        messages = db.execute(
            f"SELECT {', '.join(MESSAGE_COLUMNS)} FROM chat_messages WHERE chat_id = %s ORDER BY display_order, created_at",
            (chat_id,),
        )
        state = db.execute_one(f"SELECT {', '.join(STATE_COLUMNS)} FROM chat_workflow_state WHERE chat_id = %s", (chat_id,))
        out.append({"chat": dict(chat), "messages": [dict(m) for m in messages], "workflow_state": dict(state) if state else None})
    return out


if __name__ == "__main__":
    from .db.sqlite import SqliteDatabaseClient

    examples = export_examples(SqliteDatabaseClient(os.environ["APP_DB_PATH"]), sys.argv[1:])
    EXAMPLES_PATH.write_text(json.dumps(examples, indent=1, ensure_ascii=False) + "\n")
    print(f"wrote {len(examples)} chats to {EXAMPLES_PATH}")
