from __future__ import annotations

from typing import Callable, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field

from chat_api.storage import examples
from chat_api.storage.models.chat import Chat, ChatMessage


class CreateChatRequest(BaseModel):
    title: str = "New chat"
    agent_uuid: str | None = None


class UpdateChatRequest(BaseModel):
    title: str | None = None
    is_archived: bool | None = None
    pinned: bool | None = None


class FeedbackRequest(BaseModel):
    rating: Literal[-1, 1]
    comment: str | None = Field(default=None, max_length=2000)


def serialize_chat(chat: Chat) -> dict:
    return {
        "id": chat.id,
        "userId": chat.user_id,
        "title": chat.title,
        "agentUuid": chat.agent_uuid,
        "isArchived": chat.is_archived,
        "pinnedAt": chat.pinned_at.isoformat() if chat.pinned_at else None,
        "createdAt": chat.created_at.isoformat(),
        "updatedAt": chat.updated_at.isoformat(),
    }


def serialize_feedback(feedback) -> dict | None:
    if feedback is None:
        return None
    return {"rating": feedback.rating, "comment": feedback.comment}


def serialize_message(message: ChatMessage, feedback=None) -> dict:
    return {
        "id": message.id,
        "chatId": message.chat_id,
        "role": message.role,
        "content": message.content,
        "parts": message.parts_json,
        "metadata": message.metadata_json,
        "displayOrder": message.display_order,
        "createdAt": message.created_at.isoformat(),
        "feedback": serialize_feedback(feedback),
    }


def create_chats_router(*, storage, require_auth: Callable) -> APIRouter:
    router = APIRouter(prefix="/app/chats", tags=["chats"])

    @router.get("")
    def list_chats(
        limit: int = 20,
        offset: int = 0,
        user: dict = Depends(require_auth),
    ) -> dict:
        if examples.enabled():
            examples.seed_examples(storage, user["id"])
        chats = storage.chats.list_chats(
            user_id=user["id"],
            limit=max(1, min(limit, 100)),
            offset=max(0, offset),
        )
        return {"chats": [serialize_chat(chat) for chat in chats]}

    @router.post("")
    def create_chat(
        request: CreateChatRequest,
        user: dict = Depends(require_auth),
    ) -> dict:
        chat = storage.chats.create_chat(
            user_id=user["id"],
            title=request.title,
            agent_uuid=request.agent_uuid,
        )
        return {"chat": serialize_chat(chat)}

    @router.get("/{chat_id}")
    def get_chat(
        chat_id: str,
        user: dict = Depends(require_auth),
    ) -> dict:
        chat = storage.chats.get_chat(chat_id=chat_id, user_id=user["id"])
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        messages = storage.chats.list_messages(chat_id=chat.id, user_id=user["id"]) or []
        feedback_by_message = {
            item.message_id: item for item in storage.feedback.list_for_chat(chat.id, user["id"])
        }
        return {
            "chat": serialize_chat(chat),
            "messages": [
                serialize_message(message, feedback_by_message.get(message.id)) for message in messages
            ],
        }

    @router.patch("/{chat_id}")
    def update_chat(
        chat_id: str,
        request: UpdateChatRequest,
        user: dict = Depends(require_auth),
    ) -> dict:
        chat = storage.chats.update_chat(
            chat_id=chat_id,
            user_id=user["id"],
            title=request.title,
            is_archived=request.is_archived,
            pinned=request.pinned,
        )
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        return {"chat": serialize_chat(chat)}

    @router.delete("/{chat_id}")
    def delete_chat(
        chat_id: str,
        user: dict = Depends(require_auth),
    ) -> dict:
        chat = storage.chats.update_chat(chat_id=chat_id, user_id=user["id"], is_archived=True)
        if chat is None:
            raise HTTPException(status_code=404, detail="Chat not found")
        return {"deleted": True, "chat": serialize_chat(chat)}

    def _assistant_message(chat_id: str, message_id: str, user: dict):
        message = storage.chats.get_message(chat_id=chat_id, message_id=message_id, user_id=user["id"])
        if message is None:
            raise HTTPException(status_code=404, detail="Message not found")
        if message.role != "assistant":
            raise HTTPException(status_code=422, detail="Feedback is only accepted on assistant messages")
        return message

    @router.put("/{chat_id}/messages/{message_id}/feedback")
    def set_feedback(
        chat_id: str,
        message_id: str,
        request: FeedbackRequest,
        user: dict = Depends(require_auth),
    ) -> dict:
        _assistant_message(chat_id, message_id, user)
        feedback = storage.feedback.upsert(
            message_id=message_id,
            chat_id=chat_id,
            user_id=user["id"],
            rating=request.rating,
            comment=(request.comment or "").strip() or None,
        )
        return {"feedback": serialize_feedback(feedback)}

    @router.delete("/{chat_id}/messages/{message_id}/feedback", status_code=204)
    def clear_feedback(chat_id: str, message_id: str, user: dict = Depends(require_auth)) -> Response:
        _assistant_message(chat_id, message_id, user)
        storage.feedback.delete(message_id, user["id"])
        return Response(status_code=204)

    return router
