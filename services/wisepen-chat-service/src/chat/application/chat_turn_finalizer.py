from typing import List, Optional
from datetime import datetime, timezone
import uuid

from common.logger import log_error

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.model import ModelScope
from chat.domain.interfaces.llm import LLMProvider
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.message_lifecycle import MessageLifecycle, PersistenceMode
from chat.domain.repositories import MessageRepository, HotContextRepository, SessionRepository, ProviderRepository
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.kafka.producer import KafkaProducerClient


class ChatTurnFinalizer:
    """
    负责对话完成后的全部写入操作: Token 回填、Redis 追加、MongoDB 持久化归档、Memory 长期记忆摄入、摘要压缩、token计费
    """

    def __init__(
        self,
        llm: LLMProvider,
        memory: MemoryProvider,
        message_repo: MessageRepository,
        session_repo: SessionRepository,
        hot_context_repo: HotContextRepository,
        provider_repo: ProviderRepository,
        kafka_producer: KafkaProducerClient,
    ):
        self.llm = llm
        self.memory = memory
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.hot_context_repo = hot_context_repo
        self.provider_repo = provider_repo
        self.kafka_producer = kafka_producer

    async def _fill_token_counts(self, messages: List[ChatMessage], provider_model_name: str) -> None:
        """批量计算 token_count"""
        for msg in messages:
            if msg.content is None:
                msg.token_count = 0
            if msg.token_count is None:
                try:
                    # 调用 llm.count_tokens 计算
                    msg.token_count = await self.llm.count_tokens(msg.content, provider_model_name)
                except Exception:
                    msg.token_count = len(msg.content) // 4  # 降级为 4 字符 1 token

    async def _send_token_billing(
        self,
        user_id: str,
        resolved_model: ModelRequestInfo,
        messages: List[ChatMessage],
        group_id: Optional[str] = None,
    ) -> None:
        """
        发送 token 计费消息到 Kafka
        """
        usage_tokens = sum(msg.token_count or 0 for msg in messages)
        if usage_tokens == 0:
            return

        billing_ratio = resolved_model.billing_ratio
        billable_usage_tokens = usage_tokens * billing_ratio if resolved_model.scope == ModelScope.SYSTEM else 0

        await self.provider_repo.increment_usage(
            provider_id=resolved_model.provider_id,
            user_id=resolved_model.owner_user_id,
            usage_tokens=usage_tokens,
            billable_usage_tokens=billable_usage_tokens,
        )

        if resolved_model.scope != ModelScope.SYSTEM:
            return

        trace_id = uuid.uuid4().hex

        value = {
            "userId": user_id,
            "groupId": group_id,
            "usageTokens": usage_tokens,
            "billingRatio": billing_ratio,
            "traceId": trace_id,
            "modelName": resolved_model.model.display_name,
            "modelType": resolved_model.model.type.value,
            "requestTime": datetime.now(timezone.utc).isoformat(),
        }

        await self.kafka_producer.send(topic=settings.KAFKA_TOKEN_CONSUMPTION_TOPIC, value=value)

    @staticmethod
    def _apply_persistence_policy(new_messages: List[ChatMessage]) -> List[ChatMessage]:
        """
        Per-message 粒度的持久化策略处理，须在任何持久化动作之前调用一次。
        - DROP：整条丢弃，避免临时控制指令进入 durable 历史。
        - REDACT_CONTENT：保留消息结构但替换 content。
        - PERSIST_FULL / PERSIST_CONTENT：原样保留。

        restore_ref 会被落到 metadata 中，供后续上下文组装恢复临时注入。
        进入 Redis / Mongo / Memory 的消息一律重置为默认 lifecycle。
        """
        persistable: List[ChatMessage] = []
        for msg in new_messages:
            mode = msg.lifecycle.persistence_mode
            if mode == PersistenceMode.DROP:
                continue

            restore_ref = msg.lifecycle.restore_ref
            if restore_ref:
                msg.metadata = dict(msg.metadata or {})
                msg.metadata["restore_ref"] = restore_ref.model_dump(mode="json")

            if mode == PersistenceMode.REDACT_CONTENT:
                msg.content = (
                    f"[Redacted: tool '{msg.name or 'unknown'}' scaffolding output]"
                )
            msg.lifecycle = MessageLifecycle()
            persistable.append(msg)
        return persistable

    async def persist_all(
        self,
        user_id: str,
        session_id: str,
        resolved_model: ModelRequestInfo,
        new_messages: List[ChatMessage],
        group_id: Optional[str] = None,
    ) -> List[ChatMessage]:
        """后台统一处理所有存储逻辑: 生命周期策略裁剪 → Redis 追加 → MongoDB 落盘 → Memory 摄入 → Token 计费"""
        # 先应用生命周期持久化策略，下游所有持久化都看这份结果
        persistable = self._apply_persistence_policy(new_messages)

        await self._fill_token_counts(persistable, resolved_model.model_name)

        # Redis 追加
        try:
            await self.hot_context_repo.append_messages(session_id, persistable)
        except Exception as e:
            log_error("Redis 上下文追加", e, session=session_id)

        # MongoDB 落盘
        try:
            for msg in persistable:
                if msg.content: msg.build_search_tokens() # 构建搜索向量 (缓解中文分词问题)

            await self.message_repo.save_messages(persistable)
        except Exception as e:
            log_error("MongoDB 消息归档", e, session=session_id)

        # Memory 摄入
        try:
            await self.memory.add_interaction(user_id=user_id, messages=persistable)
        except Exception as e:
            log_error("长期记忆写入", e, user=user_id)

        # 发出 token 计费
        await self._send_token_billing(user_id=user_id,
                                        resolved_model=resolved_model,
                                        messages=persistable,
                                        group_id=group_id)
        return persistable



    async def auto_generate_title(self, session_id: str, user_id: str, user_query: str) -> None:
        """首轮对话后自动为 'New Chat' 会话生成简洁标题"""
        try:
            session = await self.session_repo.get_session(session_id)
            if session.title != "New Chat":
                return

            prompt = [
                ChatMessage(
                    session_id=session_id,
                    role=Role.SYSTEM,
                    content="You are a conversation title generator. Generate a concise conversation title based on the user's query."
                    "Requirements: Maximum 20 words, no punctuation, no quotation marks, and output the title text directly."
                ),
                ChatMessage(
                    session_id=session_id,
                    role=Role.USER,
                    content=user_query,
                )
            ]

            response = await self.llm.chat_completion(
                model_name=settings.SUMMARY_MODEL,
                messages=prompt,
                temperature=0.5,
                api_base=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
            )
            new_title = (response.content or "").strip().strip('"\'""''')
            if not new_title:
                return

            await self.session_repo.rename_session(session_id, user_id, new_title)
        except Exception as e:
            log_error("自动生成标题", e, session=session_id)

    async def generate_updated_summary(
        self,
        session_id: str,
        messages_compress_candidates: List[ChatMessage],
        existing_summary: Optional[str],
    ) -> Optional[str]:
        """生成增量摘要文本；只负责调用摘要模型，不做持久化。"""
        if not messages_compress_candidates:
            return None

        # 构建摘要输入，将 existing_summary（上一轮摘要，如有）作为前缀，拼接 messages_compress_candidates 明细，让轻量模型生成覆盖范围更广的全局摘要
        oldest_text = "\n".join(
            [f"{m.role.value}: {m.content}" for m in messages_compress_candidates]
        )
        user_content_parts = []
        if existing_summary:
            user_content_parts.append(
                f"[Existing Summary of earlier conversation]:\n{existing_summary}"
            )
        user_content_parts.append(
            f"[New conversation to incorporate]:\n{oldest_text}"
        )
        user_content_parts.append(
            "Please generate a single, updated summary that incorporates both the existing summary "
            "and the new conversation above."
        )

        summarize_prompt = [
            ChatMessage(
                session_id=session_id,
                role=Role.SYSTEM,
                content=(
                    "You are a conversation summarizer. "
                    "Produce a concise but complete summary preserving key facts, "
                    "user preferences, decisions, and important context. "
                    "Output only the summary text, no preamble or labels."
                )
            ),
            ChatMessage(
                session_id=session_id,
                role=Role.USER,
                content="\n\n".join(user_content_parts)
            )
        ]

        try:
            message_response = await self.llm.chat_completion(
                model_name=settings.SUMMARY_MODEL,
                messages=summarize_prompt,
                temperature=0.3,  # 低温，保证摘要稳定性
                api_base=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
            )
            new_summary = message_response.content or ""
        except Exception as e:
            log_error("摘要生成", e, session=session_id)
            return None

        if not new_summary.strip():
            return None

        return new_summary

    async def apply_compression_result(
        self,
        session_id: str,
        current_summary: str,
        summary_updated_at: datetime,
        messages_keep: List[ChatMessage],
    ) -> None:
        """应用已生成的摘要：更新 session summary，并用保留明细重载 Redis。"""
        if not current_summary.strip():
            return

        # 持久化新摘要到 MongoDB，同时写入压缩时间戳
        try:
            await self.session_repo.update_session_summary(
                session_id=session_id,
                current_summary=current_summary,
                summary_updated_at=summary_updated_at,
            )
        except Exception as e:
            log_error("摘要持久化", e, session=session_id)

        # Redis 重载 messages_keep
        try:
            await self.hot_context_repo.load_messages(
                session_id=session_id,
                messages=messages_keep,
            )
        except Exception as e:
            log_error("Redis 上下文重载", e, session=session_id)

    async def summarize_and_compress(
        self,
        session_id: str,
        messages_keep: List[ChatMessage],
        messages_compress_candidates: List[ChatMessage],
        existing_summary: Optional[str],
    ) -> None:
        """
        增量摘要压缩
        """
        new_summary = await self.generate_updated_summary(
            session_id=session_id,
            messages_compress_candidates=messages_compress_candidates,
            existing_summary=existing_summary,
        )
        if not new_summary:
            return

        summary_updated_at = max(
            (m.created_at for m in messages_compress_candidates),
            default=datetime.now(timezone.utc),
        )
        await self.apply_compression_result(
            session_id=session_id,
            current_summary=new_summary,
            summary_updated_at=summary_updated_at,
            messages_keep=messages_keep,
        )

    async def persist_then_apply_compression_result(
        self,
        user_id: str,
        session_id: str,
        resolved_model: ModelRequestInfo,
        new_messages: List[ChatMessage],
        current_summary: str,
        summary_updated_at: datetime,
        messages_keep: List[ChatMessage],
        group_id: Optional[str] = None,
    ) -> None:
        persistable = await self.persist_all(
            user_id=user_id,
            session_id=session_id,
            resolved_model=resolved_model,
            new_messages=new_messages,
            group_id=group_id,
        )
        await self.apply_compression_result(
            session_id=session_id,
            current_summary=current_summary,
            summary_updated_at=summary_updated_at,
            messages_keep=messages_keep + persistable,
        )

    async def persist_then_summarize_and_compress(
        self,
        user_id: str,
        session_id: str,
        resolved_model: ModelRequestInfo,
        new_messages: List[ChatMessage],
        messages_keep: List[ChatMessage],
        messages_compress_candidates: List[ChatMessage],
        existing_summary: Optional[str],
        group_id: Optional[str] = None,
    ) -> None:
        persistable = await self.persist_all(
            user_id=user_id,
            session_id=session_id,
            resolved_model=resolved_model,
            new_messages=new_messages,
            group_id=group_id,
        )
        await self.summarize_and_compress(
            session_id=session_id,
            messages_keep=messages_keep + persistable,
            messages_compress_candidates=messages_compress_candidates,
            existing_summary=existing_summary,
        )
