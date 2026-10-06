from __future__ import annotations

from typing import Any, Callable, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from chat_api.storage.models.user_settings import CUSTOM_INSTRUCTIONS_MAX, UserSettings


class UpdateSettingsRequest(BaseModel):
    """Partial update: only fields present in the body change."""

    theme: Literal["system", "light", "dark"] | None = None
    default_thinking: bool | None = None
    thinking_effort: Literal["low", "medium", "high", "extra", "max"] | None = None
    default_agent_uuid: str | None = None
    custom_instructions: str | None = Field(default=None, max_length=CUSTOM_INSTRUCTIONS_MAX)
    send_on_enter: bool | None = None
    extra: dict[str, Any] | None = None


def serialize_settings(settings: UserSettings) -> dict:
    return {
        "theme": settings.theme,
        "defaultThinking": settings.default_thinking,
        "thinkingEffort": settings.thinking_effort,
        "defaultAgentUuid": settings.default_agent_uuid,
        "customInstructions": settings.custom_instructions,
        "sendOnEnter": settings.send_on_enter,
        "extra": settings.extra,
        "updatedAt": settings.updated_at.isoformat(),
    }


def create_settings_router(*, storage, require_auth: Callable) -> APIRouter:
    router = APIRouter(prefix="/app/settings", tags=["settings"])

    @router.get("")
    def get_settings(user: dict = Depends(require_auth)) -> dict:
        return {"settings": serialize_settings(storage.user_settings.get_or_default(user["id"]))}

    @router.patch("")
    def update_settings(request: UpdateSettingsRequest, user: dict = Depends(require_auth)) -> dict:
        settings = storage.user_settings.get_or_default(user["id"])
        for field_name, value in request.model_dump(exclude_unset=True).items():
            if field_name == "custom_instructions":
                value = (value or "").strip()
            elif field_name == "extra":
                value = {**settings.extra, **(value or {})}
            setattr(settings, field_name, value)
        return {"settings": serialize_settings(storage.user_settings.upsert(settings))}

    return router
