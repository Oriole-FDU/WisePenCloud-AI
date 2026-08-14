from typing import List, Optional
from datetime import datetime, timezone
import uuid

from chat.application.agents import AgentMemoryPolicy
from chat.application.chat_context_assembler import WindowedMessages
from chat.application.token_counter import TokenCounter
from common.logger import error
from common.core.exceptions import ServiceException
from chat.domain.error_codes import ChatErrorCode

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.entities.model import ModelScope
from chat.domain.interfaces.llm import TextCompletionProvider
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
        text_llm: TextCompletionProvider,
        token_counter: TokenCounter,
        memory: MemoryProvider,
        message_repo: MessageRepository,
        session_repo: SessionRepository,
        hot_context_repo: HotContextRepository,
        provider_repo: ProviderRepository,
        kafka_producer: KafkaProducerClient,
    ):
        self.text_llm = text_llm
        self.token_counter = token_counter
        self.memory = memory
        self.session_repo = session_repo
        self.message_repo = message_repo
        self.hot_context_repo = hot_context_repo
        self.provider_repo = provider_repo
        self.kafka_producer = kafka_producer

    async def persist_message_and_token_bill(
        self,
        user_id: str,
        session_id: str,
        chat_record_messages: List[ChatMessage],
        memory_policy: AgentMemoryPolicy,
        model_info: ModelRequestInfo,
        token_usage: int,
        billing_group_id: Optional[str] = None,
        skip_first_user_message: bool = False,
    ) -> None:
        """完成正式消息持久化，并在成功后执行计费。"""
        await self.persist_messages(
            user_id=user_id,
            session_id=session_id,
            chat_record_messages=chat_record_messages,
            memory_policy=memory_policy,
            skip_first_user_message=skip_first_user_message,
        )
        try:
            await self.send_token_billing(
                user_id=user_id,
                model_info=model_info,
                token_usage=token_usage,
                billing_group_id=billing_group_id,
            )
        except Exception as exc:
            error("chat token billing failed.", session_id=session_id, exc=exc)

    async def persist_user_message(
        self,
        *,
        chat_message: ChatMessage,
        memory_policy: AgentMemoryPolicy,
    ) -> None:
        """在模型执行前持久化本轮第一条用户消息"""
        if not memory_policy.enable_persistence_chat_memory:
            return
        try:
            await self._fill_content_token_count([chat_message])
            if chat_message.content:
                chat_message.build_search_tokens()
            await self.message_repo.save_messages([chat_message])
        except Exception as e:
            error("chat user message archive failed.", session_id=chat_message.session_id, exc=e)
            raise ServiceException(ChatErrorCode.CHAT_MESSAGE_PERSIST_FAILED) from e

    async def send_token_billing(
        self,
        user_id: str,
        model_info: ModelRequestInfo,
        token_usage: int,
        billing_group_id: Optional[str] = None,
    ) -> None:
        """
        发送 token 计费消息到 Kafka
        """
        if token_usage == 0:
            return

        billable_token_usage = token_usage * model_info.billing_ratio if model_info.scope == ModelScope.SYSTEM else 0

        await self.provider_repo.increment_usage(
            provider_id=model_info.provider_id,
            user_id=model_info.owner_user_id,
            token_usage=token_usage,
            billable_token_usage=billable_token_usage,
        )

        if model_info.scope != ModelScope.SYSTEM:
            billing_group_id = None

        value = {
            "userId": user_id,
            "groupId": billing_group_id,
            "usageTokens": token_usage,
            "billingRatio": model_info.billing_ratio,
            "traceId": uuid.uuid4().hex,
            "modelName": model_info.model.display_name,
            "modelType": model_info.model.type.value,
            "requestTime": datetime.now(timezone.utc).isoformat(),
        }

        await self.kafka_producer.send(topic=settings.KAFKA_TOKEN_CONSUMPTION_TOPIC, value=value)

    async def persist_messages(
        self,
        user_id: str,
        session_id: str,
        chat_record_messages: List[ChatMessage],
        memory_policy: AgentMemoryPolicy,
        skip_first_user_message: bool = False,
    ) -> None:
        """后台统一处理所有存储逻辑: Redis 追加 → placeholder 裁剪 → MongoDB 落盘 → Memory 摄入"""

        # Redis 热上下文保留原始消息，因此先按原始内容填充 token 计数
        await self._fill_content_token_count(chat_record_messages)

        # Redis 追加
        if memory_policy.enable_chat_memory:
            try:
                await self.hot_context_repo.append_messages(session_id, chat_record_messages)
            except Exception as e:
                error("chat record message hot-context append failed.", session_id=session_id, exc=e)

        # 处理持久化占位符，如果有占位符应使用占位符替换原本的内容
        chat_record_messages = ChatMessage.for_persistence(chat_record_messages)
        # MongoDB / Memory 使用占位符裁剪后的内容，token 计数也应与裁剪后的内容一致
        await self._fill_content_token_count(chat_record_messages, force=True)

        # MongoDB 落盘 (落占位符处理的消息内容)
        if memory_policy.enable_persistence_chat_memory:
            try:
                messages_for_archive = chat_record_messages[1:] if skip_first_user_message else chat_record_messages
                for msg in messages_for_archive:
                    if msg.content: msg.build_search_tokens() # 构建搜索向量 (缓解中文分词问题)

                await self.message_repo.save_messages(messages_for_archive)
            except Exception as e:
                error("chat record message archive failed.", session_id=session_id, exc=e)
                raise ServiceException(ChatErrorCode.CHAT_MESSAGE_PERSIST_FAILED) from e

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

            response = await self.text_llm.chat_completion(
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
        chat_record_messages = ChatMessage.for_persistence(chat_record_messages)
        await self._fill_content_token_count(chat_record_messages, force=True)

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
            message_response = await self.text_llm.chat_completion(
                model_name=settings.SUMMARY_MODEL,
                messages=summarize_prompt,
                temperature=0.3,  # 低温，保证摘要稳定性
                api_base=settings.LLM_BASE_URL,
                api_key=settings.LLM_API_KEY,
            )
            new_summary = message_response.content or ""
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

    async def _fill_content_token_count(self, messages: List[ChatMessage], force: bool = False) -> None:
        for msg in messages:
            if not force and msg.content_token_count:
                continue
            msg.content_token_count = await self.token_counter.count_messages([msg])
