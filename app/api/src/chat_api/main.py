"""Entrypoint: `uvicorn chat_api.main:build_app --factory --port 7666`."""

from __future__ import annotations

import logging
import os

from chat_api.app import create_app, default_db_path


def build_app():
    logging.basicConfig(level=os.environ.get("APP_LOG_LEVEL", "INFO"))
    raw = os.environ.get("APP_CORS_ORIGINS", "")
    origins = [origin.strip() for origin in raw.split(",") if origin.strip()] or None
    return create_app(db_path=default_db_path(), cors_origins=origins)


def main() -> None:
    import uvicorn

    uvicorn.run(
        "chat_api.main:build_app",
        factory=True,
        host=os.environ.get("APP_HOST", "127.0.0.1"),  # 0.0.0.0 in docker
        port=int(os.environ.get("APP_PORT", "7666")),
        reload=os.environ.get("APP_RELOAD", "").lower() in {"1", "true", "yes"},
    )
