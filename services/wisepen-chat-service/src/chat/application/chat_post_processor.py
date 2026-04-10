from typing import List, Optional
from datetime import datetime, timezone
import uuid

from common.logger import log_error

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.model import Model, ModelType
from chat.domain.interfaces.llm import LLMProvider
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import MessageRepository, HotContextRepository, SessionRepository
from common.kafka.producer import KafkaProducerClient


class ChatPostProcessor:
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
        kafka_producer: KafkaProducerClient,
    ):
        self.llm = llm
        self.memory = memory
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.hot_context_repo = hot_context_repo
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
        model_id: int,
        messages: List[ChatMessage],
        group_id: Optional[str] = None,
    ) -> None:
        """
        发送 token 计费消息到 Kafka
        """
        usage_tokens = sum(msg.token_count for msg in messages)
        if usage_tokens == 0:
            return

        model = await Model.find_one(Model.id == model_id)
        model_type = model.type.value if model else ModelType.UNKNOWN_MODEL.value
        billing_ratio = model.billing_ratio if model else 1

        trace_id = uuid.uuid4().hex

        value = {
            "userId": user_id,
            "groupId": group_id,
            "usageTokens": usage_tokens,
            "billingRatio": billing_ratio,
            "traceId": trace_id,
            "modelName": model.display_name,
            "modelType": model_type,
            "requestTime": datetime.now(timezone.utc).isoformat(),
        }

        ok = await self.kafka_producer.send(topic=settings.KAFKA_TOKEN_CONSUMPTION_TOPIC, value=value)

        if not ok:
            log_error("Token计费消息发送", user_id=user_id, trace_id=trace_id, usage_tokens=usage_tokens)
        else:
            log_ok("Token 计费消息发送", user_id=user_id, trace_id=trace_id, usage_tokens=usage_tokens)

    async def persist_all(
        self,
        user_id: str,
        session_id: str,
        model_id: int,
        provider_model_name: str,
        new_messages: List[ChatMessage],
        group_id: Optional[str] = None,
    ) -> None:
        """后台统一处理所有存储逻辑: Redis 追加 → MongoDB 落盘 → Memory 摄入 → 摘要压缩（如有必要）"""
        await self._fill_token_counts(new_messages, provider_model_name)

        # Redis 追加
        try:
            await self.hot_context_repo.append_messages(session_id, new_messages)
        except Exception as e:
            log_error("Redis 上下文追加", e, session=session_id)

        # MongoDB 落盘
        try:
            for msg in new_messages:
                if msg.content: msg.build_search_tokens() # 构建搜索向量 (缓解中文分词问题)

            await self.message_repo.save_many(new_messages)
        except Exception as e:
            log_error("MongoDB 消息归档", e, session=session_id)

        # Memory 摄入
        try:
            await self.memory.add_interaction(user_id=user_id, messages=new_messages)
        except Exception as e:
            log_error("长期记忆写入", e, user=user_id)

        # 发出 token 计费
        await self._send_token_billing(user_id=user_id, 
                                        model_id=model_id,
                                        messages=new_messages, 
                                        group_id=group_id)



    async def auto_generate_title(self, session_id: str, user_id: str, user_query: str) -> None:
        """首轮对话后自动为 'New Chat' 会话生成简洁标题"""
        try:
            session = await self.session_repo.get_by_id(session_id)
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
            )
            new_title = (response.content or "").strip().strip('"\'""''')
            if not new_title:
                return

            await self.session_repo.rename(session_id, user_id, new_title)
        except Exception as e:
            log_error("自动生成标题", e, session=session_id)

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
            )
            new_summary = message_response.content or ""
        except Exception as e:
            log_error("摘要生成", e, session=session_id)
            return

        if not new_summary.strip():
            return

        # 持久化新摘要到 MongoDB，同时写入压缩时间戳
        try:
            await self.session_repo.update_summary(
                session_id=session_id,
                current_summary=new_summary,
                summary_updated_at=datetime.now(timezone.utc),
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
