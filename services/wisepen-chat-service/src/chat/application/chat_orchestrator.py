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
from chat.application.tools.registry import ToolRegistry
from common.kafka.producer import KafkaProducerClient


SELECTED_TEXT_TEMPLATE = """【选中内容】
{selected_text}

【用户问题】
{user_query}

【回答要求】
1. 结合上下文理解选中的内容，不要断章取义
2. 优先基于选中文本回答，必要时引用上下文辅助
3. 如果信息仍不足，可以补充你的知识，但标注「补充」
4. 引用选中文本时标注「原文」，引用上下文时标注「上下文」
5. 如果选中内容与问题无关，先指出这一点"""

class ChatOrchestrator:
    """
    Chat编排器：负责编排聊天流程中的各个环节，包含上下文管理、LLM ReAct、记忆更新等。
    公共入口 handle_chat 方法实现了从接收用户输入到生成响应的完整流程，支持异步流式输出和后置处理任务
    """

    def __init__(
            self,
            llm: LLMProvider,
            memory: MemoryProvider,
            session_repo: SessionRepository,
            message_repo: MessageRepository,
            hot_context_repo: HotContextRepository,
            tool_registry: ToolRegistry,
            kafka_producer: KafkaProducerClient
    ):
        self._memory = memory
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
            model_name: Optional[str] = None,
            states: Optional[List[Dict[str, Any]]] = None,
    ):
        resolved_model = model_name or settings.DEFAULT_MODEL

        selected_text = self._extract_selected_text(states)

        full_query = SELECTED_TEXT_TEMPLATE.format(selected_text=selected_text, user_query=user_query) if selected_text else user_query

        # [Retrieval - 短期记忆] 从 Redis 读取最近对话, 如果 Redis 缓存失效（Cache Miss），会自动从 MongoDB 回填最近的 N 条历史 （可配置），确保对话连贯性。
        recent_messages = await self._ctx.get_or_repopulate_hot_context(session_id)

        # [Retrieval - 长期记忆] 从 Memory 按相似度阈值召回跨会话事实 (此处实现是Mem0)
        relevant_facts = await self._memory.search(
            user_id=user_id, query=full_query, limit=10,
            score_threshold=0.6,  # 低质量召回直接丢弃，防止噪声污染上下文
        )

        # 会话的历史摘要
        session_summary = await self._ctx.get_session_summary(session_id)
        # [Token Window] 从后往前累加 Token，超过高水位时将 messages_compress_candidates 压缩为会话的历史摘要（本轮结束时）
        messages_keep, messages_compress_candidates, needs_compression = await self._ctx.build_context_window(recent_messages)

        # [Context Construction] 将系统提示词、Mem0 检索到的事实、会话的历史摘要以及窗口内的明细消息组装成 LLM 所需的格式
        messages_for_llm = self._ctx.assemble_prompt(
            session_id, full_query, messages_keep+messages_compress_candidates, relevant_facts, session_summary
        )

        # 记录进入 Agent 循环前的列表长度
        original_msg_count = len(messages_for_llm)

        # [Generation] 流式推理
        full_response_content = ""
        try:
            async for chunk in self._runner.stream_chat_with_tool_calling(messages_for_llm, session_id, user_id, resolved_model):
                full_response_content += chunk
                yield chunk
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
            user_msg = ChatMessage(session_id=session_id, role=Role.USER, content=full_query)
            assistant_msg = ChatMessage(session_id=session_id, role=Role.ASSISTANT, content=full_response_content)

            messages_to_persist = [user_msg] + intermediate_messages + [assistant_msg]

            background_tasks.add_task(
                self._post_processor.persist_all,
                user_id, session_id, resolved_model,
                messages_to_persist
            )
            if needs_compression:
                background_tasks.add_task(
                    self._post_processor.summarize_and_compress,
                    session_id,
                    messages_keep + messages_to_persist,
                    messages_compress_candidates,
                    session_summary
                )
    def _extract_selected_text(self, states: Optional[List[Dict[str, Any]]]) -> Optional[str]:
        if states is None:
            return None
        for state in states:
            if state.get('key') == 'selected_text' and not state.get('disabled', False):
                return state.get('value')
        return None