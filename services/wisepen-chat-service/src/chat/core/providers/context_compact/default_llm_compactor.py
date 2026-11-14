from datetime import datetime, timezone
from typing import List, Optional

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces import ContextCompactor
from chat.domain.interfaces.llm import TextCompletionProvider
from chat.domain.repositories import HotContextRepository, SessionRepository
from common.logger import error


class DefaultLlmCompactor(ContextCompactor):
    """用轻量 LLM 生成摘要的上下文压缩实现"""

    def __init__(
        self,
        *,
        text_llm: TextCompletionProvider,
        session_repo: SessionRepository,
        hot_context_repo: HotContextRepository,
    ):
        self.text_llm = text_llm
        self.session_repo = session_repo
        self.hot_context_repo = hot_context_repo

    async def auto_compact(
        self,
        *,
        session_id: str,
        history: List[ChatMessage],
        existing_summary: Optional[str],
        context_tokens: int,
        context_window_tokens: int,
        high_watermark_ratio: Optional[float] = None,
        low_watermark_ratio: Optional[float] = None,
        summary_prompt: Optional[str] = None,
    ) -> tuple[List[ChatMessage], Optional[str]]:

        high_ratio = high_watermark_ratio or settings.context.compression.high_watermark_ratio
        if context_tokens < int(context_window_tokens * high_ratio): # 无需压缩，直接返回
            return history, existing_summary

        low_ratio = low_watermark_ratio or settings.context.compression.low_watermark_ratio
        keep, candidates = self._split(history, int(context_window_tokens * low_ratio))

        return await self._compress(
            session_id=session_id, history=history, keep=keep, candidates=candidates,
            existing_summary=existing_summary, summary_prompt=summary_prompt,
        )

    async def compact(
        self,
        *,
        session_id: str,
        history: List[ChatMessage],
        existing_summary: Optional[str],
        summary_prompt: Optional[str] = None,
    ) -> tuple[List[ChatMessage], Optional[str]]:
        # 强制压缩：全部历史并入摘要，热上下文清空
        return await self._compress(
            session_id=session_id, history=history, keep=[], candidates=list(history),
            existing_summary=existing_summary, summary_prompt=summary_prompt,
        )

    async def _compress(
        self,
        *,
        session_id: str,
        history: List[ChatMessage],
        keep: List[ChatMessage],
        candidates: List[ChatMessage],
        existing_summary: Optional[str],
        summary_prompt: Optional[str],
    ) -> tuple[List[ChatMessage], Optional[str]]:
        if not candidates: # 无可压缩的内容，直接返回
            return history, existing_summary

        new_summary = await self._summarize(session_id, existing_summary, candidates, summary_prompt)
        if not new_summary: # 总结失败，下一轮再压缩，直接返回
            return history, existing_summary

        try:
            await self.session_repo.update_session_summary(
                session_id=session_id,
                current_summary=new_summary,
                summary_updated_at=datetime.now(timezone.utc),
            )
        except Exception as e:
            error("chat summary persist failed.", session_id=session_id, exc=e)
            return history, existing_summary # 更新 summary 失败，下一轮再压缩，直接返回

        try:
            await self.hot_context_repo.load_messages(session_id, keep)
        except Exception as e:
            error("redis hot context reload failed.", session_id=session_id, exc=e)

        return keep, new_summary

    @staticmethod
    def _split(history: List[ChatMessage], low_budget: int) -> tuple[List[ChatMessage], List[ChatMessage]]:
        """从后往前按 token 累加
        低水位内保留为 keep，更早的进入压缩候选
        """
        keep: List[ChatMessage] = []
        candidates: List[ChatMessage] = []
        accumulated = 0
        for msg in reversed(history):
            accumulated += msg.content_token_count or 0
            if accumulated <= low_budget:
                keep.insert(0, msg)
            else:
                candidates.insert(0, msg)
        return keep, candidates

    async def _summarize(
        self,
        session_id: str,
        existing_summary: Optional[str],
        candidates: List[ChatMessage],
        summary_prompt: Optional[str],
    ) -> Optional[str]:
        """用轻量模型把压缩候选与既有摘要合并成新的全局摘要"""
        oldest_text = "\n".join(f"{m.role.value}: {m.content}" for m in candidates)
        user_content_parts = []
        if existing_summary:
            user_content_parts.append(
                f"[Existing Summary of earlier conversation]:\n{existing_summary}"
            )
        user_content_parts.append(f"[New conversation to incorporate]:\n{oldest_text}")
        user_content_parts.append(
            "Please generate a single, updated summary that incorporates both the existing summary "
            "and the new conversation above."
        )

        summarize_prompt = [
            ChatMessage(
                session_id=session_id,
                role=Role.SYSTEM,
                content=summary_prompt or (
                    "You are a conversation summarizer. "
                    "Produce a concise but complete summary preserving key facts, "
                    "user preferences, decisions, and important context. "
                    "Output only the summary text, no preamble or labels."
                ),
            ),
            ChatMessage(
                session_id=session_id,
                role=Role.USER,
                content="\n\n".join(user_content_parts),
            ),
        ]

        try:
            response = await self.text_llm.chat_completion(
                model_name=settings.models.summary.model,
                messages=summarize_prompt,
                temperature=0.3,  # 低温，保证摘要稳定性
                api_base=settings.integrations.common_llm_provider.base_url,
                api_key=settings.integrations.common_llm_provider.api_key,
            )
            return (response.content or "").strip() or None
        except Exception as e:
            error("chat summary generation failed.", session_id=session_id, exc=e)
            return None
