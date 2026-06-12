from copy import deepcopy
from typing import List, Optional
from datetime import datetime, timezone
import uuid

from chat.application.agents import AgentMemoryPolicy
from chat.application.chat_context_assembler import WindowedMessages
from common.logger import error

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.model import ModelScope
from chat.domain.interfaces.llm import LLMProvider
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import MessageRepository, HotContextRepository, SessionRepository, ProviderRepository
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.kafka.producer import KafkaProducerClient


class ChatTurnFinalizer:
    """
    负责对话完成后的全部写入操作: Token计费、持久化（Redis 追加、MongoDB 持久化归档、Memory 长期记忆摄入）、标题生成、摘要压缩
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

    async def send_token_billing(
        self,
        user_id: str,
        resolved_model: ModelRequestInfo,
        usage_tokens: int,
        group_id: Optional[str] = None,
    ) -> None:
        """
        发送 token 计费消息到 Kafka
        """
        if usage_tokens == 0:
            return

        billable_usage_tokens = usage_tokens * resolved_model.billing_ratio if resolved_model.scope == ModelScope.SYSTEM else 0

        await self.provider_repo.increment_usage(
            provider_id=resolved_model.provider_id,
            user_id=resolved_model.owner_user_id,
            usage_tokens=usage_tokens,
            billable_usage_tokens=billable_usage_tokens,
        )

        if resolved_model.scope != ModelScope.SYSTEM:
            group_id = None

        value = {
            "userId": user_id,
            "groupId": group_id,
            "usageTokens": usage_tokens,
            "billingRatio": resolved_model.billing_ratio,
            "traceId": uuid.uuid4().hex,
            "modelName": resolved_model.model.display_name,
            "modelType": resolved_model.model.type.value,
            "requestTime": datetime.now(timezone.utc).isoformat(),
        }

        await self.kafka_producer.send(topic=settings.KAFKA_TOKEN_CONSUMPTION_TOPIC, value=value)

    @staticmethod
    def messages_placeholder_handler(chat_record_messages: List[ChatMessage]) -> List[ChatMessage]:
        chat_record_messages_with_placeholder: List[ChatMessage] = deepcopy(chat_record_messages)
        for chat_record_message in chat_record_messages_with_placeholder:
            if chat_record_message.persisted_output_placeholder is not None:
                chat_record_message.content = chat_record_message.persisted_output_placeholder
        return chat_record_messages_with_placeholder

    async def persist_messages(
        self,
        user_id: str,
        session_id: str,
        chat_record_messages: List[ChatMessage],
        memory_policy: AgentMemoryPolicy
    ) -> None:
        """后台统一处理所有存储逻辑: Redis 追加 → placeholder 裁剪 → MongoDB 落盘 → Memory 摄入"""

        # Redis 追加
        if memory_policy.enable_chat_memory:
            try:
                await self.hot_context_repo.append_messages(session_id, chat_record_messages)
            except Exception as e:
                error("chat record message hot-context append failed.", session_id=session_id, exc=e)

        # 处理持久化占位符，如果有占位符应使用占位符替换原本的内容
        chat_record_messages = self.messages_placeholder_handler(chat_record_messages)

        # MongoDB 落盘 (落占位符处理的消息内容)
        if memory_policy.enable_persistence_chat_memory:
            try:
                for msg in chat_record_messages:
                    if msg.content: msg.build_search_tokens() # 构建搜索向量 (缓解中文分词问题)

                await self.message_repo.save_messages(chat_record_messages)
            except Exception as e:
                error("chat record message archive failed.", session_id=session_id, exc=e)

        # Memory 摄入 (摄入占位符处理的消息内容)
        if memory_policy.enable_long_term_memory:
            try:
                await self.memory.add_interaction(user_id=user_id, messages=chat_record_messages)
            except Exception as e:
                error("chat record message write long-term memory failed.", user_id=user_id, exc=e)


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
            new_title = (response.raw.choices[0].message.content or "").strip().strip('"\'""''')
            if not new_title:
                return

            await self.session_repo.rename_session(session_id, user_id, new_title)
        except Exception as e:
            error("chat title generation failed.", session_id=session_id, exc=e)

    async def summarize_and_compress(
        self,
        session_id: str,
        existing_summary: Optional[str],
        windowed_history_messages: WindowedMessages | None,
        chat_record_messages: List[ChatMessage] | None,
        memory_policy: AgentMemoryPolicy,
    ) -> None:
        """
        增量摘要压缩
        """
        if windowed_history_messages is None or chat_record_messages is None:
            return

        # 处理持久化占位符，如果有占位符应使用占位符替换原本的内容
        chat_record_messages = self.messages_placeholder_handler(chat_record_messages)

        # 构建摘要输入，将 existing_summary（上一轮摘要，如有）作为前缀，拼接 messages_compress_candidates 明细，让轻量模型生成覆盖范围更广的全局摘要
        oldest_text = "\n".join(
            [f"{m.role.value}: {m.content}" for m in windowed_history_messages.messages_compress_candidates]
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
                content=memory_policy.summary_prompt or (
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
            new_summary = message_response.raw.choices[0].message.content or ""
        except Exception as e:
            error("chat summary generation failed.", session_id=session_id, exc=e)
            return

        if not new_summary.strip():
            return

        # 持久化新摘要到 MongoDB，同时写入压缩时间戳
        try:
            await self.session_repo.update_session_summary(session_id=session_id, current_summary=new_summary,
                                                           summary_updated_at=datetime.now(timezone.utc))
        except Exception as e:
            error("chat summary persist failed.", session_id=session_id, exc=e)

        # Redis 重载 messages_keep
        try:
            await self.hot_context_repo.load_messages(
                session_id=session_id,
                messages=windowed_history_messages.messages_keep + chat_record_messages,
            )
        except Exception as e:
            error("redis hot context reload failed.", session_id=session_id, exc=e)

