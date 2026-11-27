from datetime import datetime, timezone
from typing import Dict, List

from chat.domain.entities import ChatMessageFeedback
from chat.domain.repositories import MessageFeedbackRepository


class MongoMessageFeedbackRepository(MessageFeedbackRepository):

    async def upsert(self, feedback: ChatMessageFeedback) -> None:
        now = datetime.now(timezone.utc)
        await ChatMessageFeedback.get_pymongo_collection().update_one(
            {"user_id": feedback.user_id, "message_id": feedback.message_id},
            {
                "$set": {
                    "session_id": feedback.session_id,
                    "feedback": feedback.feedback.value,
                    "updated_at": now,
                },
                "$setOnInsert": {
                    "user_id": feedback.user_id,
                    "message_id": feedback.message_id,
                    "created_at": now,
                },
            },
            upsert=True,
        )

    async def delete(self, user_id: str, message_id: str) -> None:
        await ChatMessageFeedback.get_pymongo_collection().delete_one(
            {"user_id": user_id, "message_id": message_id},
        )

    async def list_for_user_messages(self, user_id: str, message_ids: List[str]) -> Dict[str, str]:
        if not message_ids:
            return {}
        items = await ChatMessageFeedback.find(
            {"user_id": user_id, "message_id": {"$in": message_ids}},
        ).to_list()
        return {str(item.message_id): item.feedback.value for item in items}
