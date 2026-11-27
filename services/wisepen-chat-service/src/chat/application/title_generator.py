from typing import List, Optional

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces.llm import TextCompletionProvider
from common.logger import error


class TitleGenerator:
    """根据近期对话生成会话标题（用户主动触发，区别于首轮自动生成）"""

    def __init__(self, text_llm: TextCompletionProvider):
        self._text_llm = text_llm

    async def generate_from_messages(self, session_id: str, messages: List[ChatMessage]) -> Optional[str]:
        if not messages:
            return None
        transcript = self._build_transcript(messages)
        if not transcript:
            return None
        prompt = [
            ChatMessage(
                session_id=session_id,
                role=Role.SYSTEM,
                content=(
                    "You are a conversation title generator. Generate a concise conversation title "
                    "based on the recent conversation. "
                    "Requirements: Maximum 20 words, no punctuation, no quotation marks, "
                    "and output the title text directly."
                ),
            ),
            ChatMessage(session_id=session_id, role=Role.USER, content=transcript),
        ]
        try:
            response = await self._text_llm.chat_completion(
                messages=prompt,
                model_name=settings.models.summary.model,
                temperature=0.5,
                api_base=settings.integrations.common_llm_provider.base_url,
                api_key=settings.integrations.common_llm_provider.api_key,
            )
        except Exception as e:
            error("chat title generation failed.", session_id=session_id, exc=e)
            return None
        title = (response.content or "").strip().strip("\"'“”‘’").strip()
        return title or None

    @staticmethod
    def _build_transcript(messages: List[ChatMessage]) -> str:
        lines = []
        for msg in messages:
            if msg.role == Role.USER:
                lines.append(f"用户：{msg.content or ''}")
            elif msg.role == Role.ASSISTANT:
                lines.append(f"助手：{msg.content or ''}")
        return "\n".join(lines).strip()
