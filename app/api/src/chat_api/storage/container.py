from __future__ import annotations

from dataclasses import dataclass

from .db.base import DatabaseClient
from .repositories.chat import ChatRepository
from .repositories.feedback import MessageFeedbackRepository
from .repositories.usage import UsageEventRepository
from .repositories.user_settings import UserSettingsRepository
from .repositories.workflow_state import WorkflowStateRepository
from .services.chat import ChatService


@dataclass
class StorageContainer:
    db: DatabaseClient
    chats: ChatService
    user_settings: UserSettingsRepository
    feedback: MessageFeedbackRepository
    usage: UsageEventRepository
    workflow_state: WorkflowStateRepository

    @classmethod
    def from_db(cls, db: DatabaseClient) -> "StorageContainer":
        return cls(
            db=db,
            chats=ChatService(ChatRepository(db)),
            user_settings=UserSettingsRepository(db),
            feedback=MessageFeedbackRepository(db),
            usage=UsageEventRepository(db),
            workflow_state=WorkflowStateRepository(db),
        )
