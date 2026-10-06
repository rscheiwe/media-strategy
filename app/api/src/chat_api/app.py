"""FastAPI app factory for the chat API."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

load_dotenv()  # app/api/.env or the working directory's .env, if present

from chat_api.casedata import ensure_case_db  # noqa: E402
from chat_api.identity import current_user  # noqa: E402
from chat_api.chat.agent import create_chat_agent  # noqa: E402
from chat_api.chat.router import create_chat_router  # noqa: E402
from chat_api.routes.chats import create_chats_router  # noqa: E402
from chat_api.routes.settings import create_settings_router  # noqa: E402
from chat_api.routes.usage import create_usage_router  # noqa: E402
from chat_api.storage.container import StorageContainer  # noqa: E402
from chat_api.storage.db.sqlite import SqliteDatabaseClient  # noqa: E402

log = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = [
    "http://localhost:5666",
    "http://127.0.0.1:5666",
    "http://localhost:8080",
    "http://127.0.0.1:8080",
]
DEFAULT_DB_PATH = str(Path("data") / "chat.db")


def default_db_path() -> str:
    """APP_DB_PATH: a SQLite file (default ./data/chat.db; /data/chat.db in docker) or :memory:."""
    return (os.getenv("APP_DB_PATH") or DEFAULT_DB_PATH).strip()


def create_app(
    *,
    db_path: str = ":memory:",
    agent_factory=create_chat_agent,
    cors_origins: list[str] | None = None,
    storage: StorageContainer | None = None,
) -> FastAPI:
    """Build the API. Tests use the default in-memory database; main.py passes APP_DB_PATH."""
    if storage is None:
        storage = StorageContainer.from_db(SqliteDatabaseClient(db_path))
    # The case data (products, history, inventory, briefs) is rebuilt from its source
    # files on every start, so it never drifts from them. Missing sources are logged,
    # not fatal: chat still works, and the data tools report the problem.
    try:
        log.info("case data: %s", ensure_case_db(rebuild=True))
    except FileNotFoundError as exc:
        log.error("case data not built: %s", exc)

    app = FastAPI(title="Chat API", version="0.1.0", dependencies=[Depends(current_user)])
    app.state.storage = storage

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins or DEFAULT_CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(create_chats_router(storage=storage, require_auth=current_user))
    app.include_router(create_settings_router(storage=storage, require_auth=current_user))
    app.include_router(create_usage_router(storage=storage, require_auth=current_user))
    app.include_router(
        create_chat_router(
            chat_service=storage.chats,
            api_key=os.getenv("OPENAI_API_KEY", ""),
            require_auth=current_user,
            agent_factory=agent_factory,
            settings_repo=storage.user_settings,
            usage_repo=storage.usage,
            workflow_state=storage.workflow_state,
        )
    )
    return app
