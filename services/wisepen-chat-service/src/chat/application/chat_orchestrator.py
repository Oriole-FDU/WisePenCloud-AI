from typing import Optional, List, Dict, Any
from fastapi import BackgroundTasks
from common.logger import log_error, log_ok

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.interfaces.llm import LLMProvider
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import SessionRepository, MessageRepository, HotContextRepository
from common.core.exceptions import ServiceException
from chat.application.context_manager import ContextManager
from chat.application.llm_runner import LLMRunner
from chat.application.chat_post_processor import ChatPostProcessor
from chat.application.model_resolver import ModelResolver
from chat.application.tools.registry import ToolRegistry
from common.kafka.producer import KafkaProducerClient


class ChatOrchestrator:
    """
    Chat编排器：负责编排聊天流程中的各个环节，包含上下文管理、LLM ReAct、记忆更新等。
    公共入口 handle_chat 方法实现了从接收用户输入到生成响应的完整流程，支持异步流式输出和后置处理任务
    """

    def __init__(
            self,
            llm: LLMProvider,
            memory: MemoryProvider,
            model_resolver: ModelResolver,
            session_repo: SessionRepository,
            message_repo: MessageRepository,
            hot_context_repo: HotContextRepository,
            tool_registry: ToolRegistry,
            kafka_producer: KafkaProducerClient
    ):
        self._memory = memory
        self._model_resolver = model_resolver
        self._ctx = ContextManager(
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo
        )
        self._runner = LLMRunner(llm=llm, tool_registry=tool_registry)
        self._post_processor = ChatPostProcessor(
            llm=llm, memory=memory,
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo,
            kafka_producer=kafka_producer
        )

    # -------------------------------------------------------------------------
    # 公共入口
    # -------------------------------------------------------------------------
    async def handle_chat(
            self,
            user_id: str,
            session_id: str,
            user_query: str,
            background_tasks: BackgroundTasks,
            model_id: Optional[int] = None,
            states: Optional[List[Dict[str, Any]]] = None,
    ):
        model_id = model_id or settings.DEFAULT_MODEL

        # [Model Resolve] 通过映射表查找首选供应商，获取实际模型名和 API 凭证
        resolved = await self._model_resolver.resolve(model_id)

        # [Retrieval - 短期记忆] 从 Redis 读取最近对话, 如果 Redis 缓存失效（Cache Miss），会自动从 MongoDB 回填最近的 N 条历史 （可配置），确保对话连贯性。
        recent_messages = await self._ctx.get_or_repopulate_hot_context(session_id)

        # [Retrieval - 长期记忆] 从 Memory 按相似度阈值召回跨会话事实 (此处实现是Mem0)
        relevant_facts = await self._memory.search(
            user_id=user_id, query=user_query, limit=10,
            score_threshold=0.6,  # 低质量召回直接丢弃，防止噪声污染上下文
        )

        # 会话的历史摘要
        session_summary = await self._ctx.get_session_summary(session_id)
        # [Token Window] 从后往前累加 Token，超过高水位时将 messages_compress_candidates 压缩为会话的历史摘要（本轮结束时）
        messages_keep, messages_compress_candidates, needs_compression = await self._ctx.build_context_window(recent_messages)

        # [Context Construction] 将系统提示词、Mem0 检索到的事实、会话的历史摘要、前端上下文以及窗口内的明细消息组装成 LLM 所需的格式
        messages_for_llm = self._ctx.assemble_prompt(
            session_id, user_query, messages_keep+messages_compress_candidates, relevant_facts, session_summary,
            states=states,
        )

        # 记录进入 Agent 循环前的列表长度
        original_msg_count = len(messages_for_llm)

        # [Generation] 流式推理，使用解析后的供应商模型名和凭证
        full_response_content = ""
        try:
            async for event in self._runner.stream_chat_with_tool_calling(
                messages_for_llm, session_id, user_id,
                model_name=resolved.provider_model_name,
                model_id=model_id,
                api_base=resolved.api_base_url,
                api_key=resolved.api_key,
            ):
                # event.content 仅在文本增量时非空，其他协议事件为 ""
                full_response_content += event.content
                yield event.sse
        except ServiceException as e:
            log_error("LLM 流式推理", e, session=session_id)
            yield f"\n[System Error]: {e.msg}"
            return

        # 通过切片，提取出 LLMRunner 在运行过程中追加的所有中间消息（Tool Calls & Results）
        intermediate_messages = messages_for_llm[original_msg_count:]

        # [Persistence] 使用 FastAPI 的 BackgroundTasks 在响应返回给用户后，异步执行
        #   - _post_processor.persist_all：将新消息写入 Redis 和 MongoDB；将新对话摄入 Memory 长期记忆
        #   - _post_processor.summarize_and_compress；调用轻量级模型生成并更新会话的全局摘要
        if background_tasks is not None:
            user_msg = ChatMessage(
                session_id=session_id, role=Role.USER, content=user_query,
                metadata={"states": states} if states else {},
            )
            assistant_msg = ChatMessage(session_id=session_id, role=Role.ASSISTANT, content=full_response_content, model_id=model_id)

            messages_to_persist = [user_msg] + intermediate_messages + [assistant_msg]

            background_tasks.add_task(
                self._post_processor.persist_all,
                user_id, session_id, model_id,
                resolved.provider_model_name,
                messages_to_persist
            )
            background_tasks.add_task(
                self._post_processor.auto_generate_title,
                session_id, user_id, user_query
            )
            if needs_compression:
                background_tasks.add_task(
                    self._post_processor.summarize_and_compress,
                    session_id,
                    messages_keep + messages_to_persist,
                    messages_compress_candidates,
                    session_summary
                )