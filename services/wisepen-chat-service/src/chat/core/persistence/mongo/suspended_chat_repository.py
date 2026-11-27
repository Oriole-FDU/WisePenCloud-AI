from typing import List

from beanie import PydanticObjectId

from chat.domain.entities import SuspendedChat
from chat.domain.repositories import SuspendedChatRepository


class MongoSuspendedChatRepository(SuspendedChatRepository):
    @staticmethod
    def _from_raw(raw: dict | None) -> SuspendedChat | None:
        return SuspendedChat.model_validate(raw) if raw is not None else None

    async def create(self, suspended_chat: SuspendedChat) -> SuspendedChat:
        return await suspended_chat.insert()

    async def find_suspended_by_session(self, session_id: str, user_id: str) -> SuspendedChat | None:
        suspended_chat = await SuspendedChat.find(
            SuspendedChat.session_id == session_id,
            SuspendedChat.user_id == user_id,
        ).sort("-updated_at").first_or_none()

        return suspended_chat

    async def find_suspended_by_sessions(self, session_ids: List[str], user_id: str) -> dict[str, SuspendedChat]:
        if not session_ids:
            return {}
        suspended_chats = await SuspendedChat.find(
            {"session_id": {"$in": session_ids}, "user_id": user_id},
        ).to_list()
        return {sc.session_id: sc for sc in suspended_chats}

    async def delete_by_id(self, suspended_chat_id: str) -> None:
        await SuspendedChat.get_pymongo_collection().delete_one({
            "_id": PydanticObjectId(suspended_chat_id),
        })

    async def delete_suspended_by_session(self, session_id: str, user_id: str) -> None:
        suspended_chat = await SuspendedChat.find(
            SuspendedChat.session_id == session_id,
            SuspendedChat.user_id == user_id,
        ).sort("-updated_at").first_or_none()

        if suspended_chat is None:
            return

        await SuspendedChat.get_pymongo_collection().delete_one({"_id": suspended_chat.id})
