from typing import Optional, List, Dict, Any
from beanie import PydanticObjectId
from fastapi import BackgroundTasks
from common.logger import log_error

from chat.api.schemas.chat import AttachmentRefRequest
from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces.llm import LLMProvider
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import SessionRepository, MessageRepository, HotContextRepository, ModelRepository, ProviderRepository
from common.core.exceptions import ServiceException
from chat.application.chat_context_assembler import ChatContextAssembler
from chat.application.query_loop_runtime import (
    QueryLoopRuntime,
    StepStartEvent,
    TextDeltaEvent,
    ReasoningDeltaEvent,
)
from chat.api.vercel_sse_mapper import to_vercel_sse
from chat.application.chat_turn_finalizer import ChatTurnFinalizer
from chat.application.skill_matcher import SkillMatcher
from chat.application.tools.tool_registry import ToolRegistry
from common.kafka.producer import KafkaProducerClient


# Skill 脚手架工具的名字集合：Registry 内部它们 reserved=True 默认隐藏
# 只有 skill 命中时 Coordinator 把本集合作为 `expose` 传入 derive()，从而解禁 schema
_SKILL_TOOL_NAMES = frozenset({"load_skill", "load_skill_asset"})


class ChatTurnCoordinator:
    """
    Chat协调器：负责编排聊天流程中的各个环节，包含上下文管理、LLM ReAct、记忆更新等。
    公共入口 handle_chat 方法实现了从接收用户输入到生成响应的完整流程，支持异步流式输出和后置处理任务
    """

    def __init__(
            self,
            llm: LLMProvider,
            memory: MemoryProvider,
            model_repo: ModelRepository,
            provider_repo: ProviderRepository,
            session_repo: SessionRepository,
            message_repo: MessageRepository,
            hot_context_repo: HotContextRepository,
            tool_registry: ToolRegistry,
            kafka_producer: KafkaProducerClient,
            skill_matcher: SkillMatcher,
    ):
        self._memory = memory
        self._model_repo = model_repo
        self._context_assembler = ChatContextAssembler(
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo
        )
        self._tool_registry = tool_registry
        self._query_loop_runtime = QueryLoopRuntime(llm=llm)
        self._turn_finalizer = ChatTurnFinalizer(
            llm=llm, memory=memory,
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo,
            provider_repo=provider_repo,
            kafka_producer=kafka_producer
        )
        self._skill_matcher = skill_matcher

    # -------------------------------------------------------------------------
    # 公共入口
    # -------------------------------------------------------------------------
    async def handle_chat(
            self,
            user_id: str,
            session_id: str,
            user_query: str,
            background_tasks: BackgroundTasks,
            model_id: PydanticObjectId,
            provider_id: Optional[PydanticObjectId] = None,
            states: Optional[List[Dict[str, Any]]] = None,
            attachment_refs: Optional[List[AttachmentRefRequest]] = None,
            attachments_meta: Optional[List[Dict[str, Any]]] = None,
    ):
        # [Model Resolve] 通过仓储解析模型、映射、供应商和 API 凭证
        resolved = await self._model_repo.resolve_model_for_chat(
            model_id=model_id,
            user_id=user_id,
            provider_id=provider_id,
        )

        context_limit = resolved.context_window_tokens or settings.CTX_TOKEN_LIMIT
        output_reserve = resolved.max_output_tokens or settings.CTX_DEFAULT_OUTPUT_RESERVE_TOKENS
        prompt_budget_tokens = max(
            context_limit - output_reserve,
            settings.CTX_MIN_PROMPT_BUDGET_TOKENS,
        )

        # [Retrieval - 短期记忆] 从 Redis 读取最近对话, 如果 Redis 缓存失效（Cache Miss），会自动从 MongoDB 回填最近的 N 条历史 （可配置），确保对话连贯性。
        recent_messages = await self._context_assembler.get_or_repopulate_hot_context(session_id)

        # [Retrieval - 长期记忆] 从 Memory 按相似度阈值召回跨会话事实 (此处实现是Mem0)
        relevant_facts = await self._memory.search(
            user_id=user_id, query=effective_user_query, limit=10,
            score_threshold=0.6,  # 低质量召回直接丢弃，防止噪声污染上下文
        )

        # 会话的历史摘要
        session_summary = await self._context_assembler.get_session_summary(session_id)
        # [Token Window] 从后往前累加 Token，超过高水位时将 messages_compress_candidates 压缩为会话的历史摘要（本轮结束时）
        messages_keep, messages_compress_candidates, needs_compression = await self._context_assembler.build_context_window(
            recent_messages,
            prompt_budget_tokens=prompt_budget_tokens,
        )

        tool_context: dict[str, Any] = {
            "session_id": session_id,
            "user_id": user_id,
        } 

        # [Skill Match] 预筛当前 query 可能相关的 Skill，命中才暴露 schema + 注入 Available Skills
        candidate_skills = self._skill_matcher.match(effective_user_query)
        expose_tool_name_set = None
        if candidate_skills:
            # 解禁 Registry 里默认隐藏的 skill 脚手架工具（reserved=True）
            expose_tool_name_set = set(_SKILL_TOOL_NAMES)
            tool_context["allowed_skill_ids"] = [s.skill_id for s in candidate_skills]

        # [Tool Scope] 派生本请求的工具视图快照
        # expose_tool_name_set 仅在 skill 命中时解禁 load_skill 系列，未命中时它们保持隐藏
        # runtime_discovered_tools 预留给"运行时动态发现的工具"（如 Skill bundle 自带 tools），暂时留空
        # allow_tool_name_set/deny_tool_name_set 预留给未来"用户级工具偏好"接入，暂时留空
        tool_scope = self._tool_registry.derive(
            session_id=session_id,
            tool_context=tool_context, 
            runtime_discovered_tools=None,
            expose_tool_name_set=expose_tool_name_set,
            allow_tool_name_set=None,
            deny_tool_name_set=None,
        )

        # [Context Construction] 将系统提示词、Mem0 检索到的事实、会话的历史摘要、前端上下文以及窗口内的明细消息组装成 LLM 所需的格式
        messages_for_llm = self._context_assembler.assemble_prompt(
            session_id, effective_user_query, hydrated_window_messages, relevant_facts, session_summary,
            states=merged_states,
            candidate_skills=candidate_skills or None,
            attachments_meta=attachments_meta,
        )

        # 记录进入 Agent 循环前的列表长度
        original_msg_count = len(messages_for_llm)

        # 在流式推理之前构造 user_msg，确保 created_at 早于所有中间消息
        user_msg = ChatMessage(
            session_id=session_id, role=Role.USER, content=effective_user_query,
            metadata={
                "states": merged_states,
                "attachment_refs": [ref.model_dump() for ref in (attachment_refs or [])],
            },
        )

        # [Generation] 流式推理，使用解析后的供应商模型名和凭证
        full_response_content = ""
        full_reasoning_content = ""
        try:
            async for event in self._query_loop_runtime.stream_chat_with_tool_calling(
                messages_for_llm,
                tool_scope=tool_scope,
                session_id=session_id,
                model_name=resolved.model_name,
                model_id=resolved.model_id,
                api_base=resolved.api_base_url,
                api_key=resolved.api_key,
            ):
                # QueryLoopRuntime 只产出领域事件；这里按需累加纯文本，并把事件翻译为 Vercel SSE 字符串
                if isinstance(event, StepStartEvent):
                    full_reasoning_content = ""
                    full_response_content = ""
                elif isinstance(event, TextDeltaEvent):
                    full_response_content += event.delta
                elif isinstance(event, ReasoningDeltaEvent):
                    full_reasoning_content += event.delta
                yield to_vercel_sse(event)
        except ServiceException as e:
            log_error("LLM 流式推理", e, session=session_id)
            yield f"\n[System Error]: {e.msg}"
            return

        # 通过切片，提取出 QueryLoopRuntime 在运行过程中追加的所有中间消息（Tool Calls & Results）
        intermediate_messages = messages_for_llm[original_msg_count:]

        # [Persistence] 使用 FastAPI 的 BackgroundTasks 在响应返回给用户后，异步执行
        #   - _turn_finalizer.persist_all：将新消息写入 Redis 和 MongoDB；将新对话摄入 Memory 长期记忆
        #   - _turn_finalizer.summarize_and_compress；调用轻量级模型生成并更新会话的全局摘要
        if background_tasks is not None:
            assistant_msg = ChatMessage(
                session_id=session_id, role=Role.ASSISTANT, content=full_response_content,
                reasoning_content=full_reasoning_content or None,
                model_id=resolved.model_id,
            )

            messages_to_persist = [user_msg] + intermediate_messages + [assistant_msg]

            background_tasks.add_task(
                self._turn_finalizer.persist_all,
                user_id, session_id, resolved,
                messages_to_persist
            )
            background_tasks.add_task(
                self._turn_finalizer.auto_generate_title,
                session_id, user_id, effective_user_query
            )
            if needs_compression:
                background_tasks.add_task(
                    self._turn_finalizer.summarize_and_compress,
                    session_id,
                    messages_keep + messages_to_persist,
                    messages_compress_candidates,
                    session_summary
                )
