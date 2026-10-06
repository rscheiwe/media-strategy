"""Who the caller is. There is no login and no authentication.

The caller may name itself with an `X-User-Id` header; without one, requests act
as the default user. This keeps per-user chat ownership (and its tests) intact.
Anyone can send any id, so this is identity, not access control.
"""

from __future__ import annotations

import os

from fastapi import Header, Request

DEFAULT_USER_ID = "00000000-0000-0000-0000-000000000001"


def default_user_id() -> str:
    return (os.getenv("APP_DEFAULT_USER_ID") or DEFAULT_USER_ID).strip()


def current_user(request: Request, x_user_id: str | None = Header(default=None)) -> dict:
    """FastAPI dependency: the caller's user record, from X-User-Id or the default."""
    cached = getattr(request.state, "user", None)
    if cached is not None:
        return cached
    user = {"id": (x_user_id or "").strip() or default_user_id(), "email": None}
    request.state.user = user
    return user
