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
        collection = SuspendedChat.get_pymongo_collection()
        raw = await collection.find_one({"session_id": session_id, "user_id": user_id}, sort=[("updated_at", -1)])
        suspended_chat = self._from_raw(raw)
        if suspended_chat is not None and suspended_chat.context.model_info.api_key:
            # 读取旧记录时去掉历史凭证；CAS 避免覆盖同时更新的挂起状态。
            encoded = suspended_chat.model_dump(include={"context"})["context"]
            await collection.update_one(
                {"_id": raw["_id"], "context": raw["context"]},
                {"$set": {"context": encoded}},
            )
            suspended_chat = self._from_raw({**raw, "context": encoded})
        return suspended_chat

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
