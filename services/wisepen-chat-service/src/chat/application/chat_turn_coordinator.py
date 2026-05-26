from dataclasses import dataclass
from datetime import datetime
from typing import Optional, List, Dict, Any
from beanie import PydanticObjectId
from fastapi import BackgroundTasks
from common.logger import log_error, log_ok

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces.llm import LLMProvider
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import SessionRepository, MessageRepository, HotContextRepository, ModelRepository, ProviderRepository, SkillRepository
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
# 只有存在可用 skill 时 Coordinator 把本集合作为 `expose` 传入 derive()，从而解禁 schema
_SKILL_TOOL_NAMES = frozenset({"load_skill", "load_skill_asset"})
_HISTORY_SEARCH_TOOL_NAMES = frozenset({"search_historical_messages"})


@dataclass(frozen=True)
class PromptAssemblyResult:
    messages_for_llm: List[ChatMessage]
    messages_keep_for_hot_context: List[ChatMessage]
    precomputed_summary: Optional[str]
    summary_updated_at: Optional[datetime]


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
            skill_repo: SkillRepository,
    ):
        self._memory = memory
        self._model_repo = model_repo
        self._context_assembler = ChatContextAssembler(
            message_repo=message_repo,
            session_repo=session_repo,
            hot_context_repo=hot_context_repo,
            skill_repo=skill_repo,
        )
        self._tool_registry = tool_registry
        self._llm = llm
        self._query_loop_runtime = QueryLoopRuntime(llm=llm)
        self._turn_finalizer = ChatTurnFinalizer(
            llm=llm, memory=memory,
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo,
            provider_repo=provider_repo,
            kafka_producer=kafka_producer
        )
        self._skill_matcher = skill_matcher

    @staticmethod
    def _group_history_messages(messages: List[ChatMessage]) -> List[List[ChatMessage]]:
        groups: List[List[ChatMessage]] = []
        current: List[ChatMessage] = []
        for msg in sorted(messages, key=lambda m: m.created_at):
            if msg.role == Role.USER and current:
                groups.append(current)
                current = [msg]
            else:
                current.append(msg)
        if current:
            groups.append(current)
        return groups

    async def _count_final_prompt_tokens(
        self,
        messages: List[ChatMessage],
        tool_schemas: List[Dict[str, Any]],
        model_name: str,
    ) -> int:
        text = QueryLoopRuntime._serialize_prompt_for_counting(messages, tool_schemas)
        return await self._llm.count_tokens(text, model_name)

    async def _assemble_prompt_with_final_budget(
        self,
        *,
        session_id: str,
        user_query: str,
        messages_keep: List[ChatMessage],
        messages_compress_candidates: List[ChatMessage],
        relevant_facts: List[str],
        session_summary: Optional[str],
        states: Optional[List[Dict[str, Any]]],
        available_skills,
        tool_scope,
        model_name: str,
        prompt_budget_tokens: int,
    ) -> PromptAssemblyResult:
        keep_groups = self._group_history_messages(messages_keep)
        compress_block = sorted(messages_compress_candidates, key=lambda m: m.created_at)
        precomputed_summary: Optional[str] = None
        summary_updated_at: Optional[datetime] = None
        tool_schemas = tool_scope.schemas()

        while True:
            candidate_window = [msg for group in keep_groups for msg in group]
            summary_for_prompt = precomputed_summary or session_summary
            if precomputed_summary is None:
                candidate_window = compress_block + candidate_window

            restored_window_messages = await self._context_assembler.restore_context_injections(
                session_id,
                candidate_window,
            )
            messages_for_llm = self._context_assembler.assemble_prompt(
                session_id,
                user_query,
                restored_window_messages,
                relevant_facts,
                summary_for_prompt,
                states=states,
                available_skills=available_skills or None,
            )
            token_count = await self._count_final_prompt_tokens(
                messages_for_llm,
                tool_schemas,
                model_name,
            )
            if token_count <= prompt_budget_tokens:
                return PromptAssemblyResult(
                    messages_for_llm=messages_for_llm,
                    messages_keep_for_hot_context=candidate_window,
                    precomputed_summary=precomputed_summary,
                    summary_updated_at=summary_updated_at,
                )

            if not compress_block and keep_groups:
                compress_block.extend(keep_groups.pop(0))
            elif precomputed_summary is not None and keep_groups:
                compress_block.extend(keep_groups.pop(0))
            elif precomputed_summary is not None:
                raise ServiceException(
                    ChatErrorCode.CONTEXT_LIMIT_EXCEEDED,
                    custom_msg=(
                        "最终提示词在压缩全部可压缩历史后仍超出模型上下文预算，"
                        f"tokens={token_count}, budget={prompt_budget_tokens}"
                    ),
                )
            elif not compress_block:
                raise ServiceException(
                    ChatErrorCode.CONTEXT_LIMIT_EXCEEDED,
                    custom_msg=(
                        "最终提示词在没有可压缩历史明细后仍超出模型上下文预算，"
                        f"tokens={token_count}, budget={prompt_budget_tokens}"
                    ),
                )

            precomputed_summary = await self._turn_finalizer.generate_updated_summary(
                session_id=session_id,
                messages_compress_candidates=compress_block,
                existing_summary=session_summary,
            )
            if not precomputed_summary:
                raise ServiceException(
                    ChatErrorCode.CONTEXT_LIMIT_EXCEEDED,
                    custom_msg=(
                        "最终提示词超出模型上下文预算，且同步摘要生成失败，"
                        f"tokens={token_count}, budget={prompt_budget_tokens}"
                    ),
                )
            summary_updated_at = max(m.created_at for m in compress_block)

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
            user_id=user_id, query=user_query, limit=10,
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
            "turn_loaded_files": set(),
        } 

        # [Skill Discovery] 注入当前会话可用 Skill 的轻量 metadata，由 LLM 决定是否调用 load_skill。
        # 不再用字符串 triggers 预筛，避免关键词未命中导致 Skill 无法被模型看见。
        available_skills = self._skill_matcher.match(user_query)
        expose_tool_name_set = set()
        if available_skills:
            # 解禁 Registry 里默认隐藏的 skill 脚手架工具（reserved=True）
            expose_tool_name_set.update(_SKILL_TOOL_NAMES)
            tool_context["allowed_skill_ids"] = [s.skill_id for s in available_skills]

        # 历史搜索工具默认隐藏。只有当前会话已经有摘要（说明早期明细发生过压缩）时，
        # 才解禁给模型，避免短会话/未压缩会话里频繁冗余检索。
        history_search_disabled = not bool(session_summary)
        if not history_search_disabled:
            expose_tool_name_set.update(_HISTORY_SEARCH_TOOL_NAMES)

        expose_tool_name_set = expose_tool_name_set or None

        # [Tool Scope] 派生本请求的工具视图快照
        # expose_tool_name_set 仅在满足系统条件时解禁 reserved 工具，否则它们保持隐藏
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
        prompt_assembly = await self._assemble_prompt_with_final_budget(
            session_id=session_id,
            user_query=user_query,
            messages_keep=messages_keep,
            messages_compress_candidates=messages_compress_candidates,
            relevant_facts=relevant_facts,
            session_summary=session_summary,
            states=states,
            available_skills=available_skills,
            tool_scope=tool_scope,
            model_name=resolved.model_name,
            prompt_budget_tokens=prompt_budget_tokens,
        )
        messages_for_llm = prompt_assembly.messages_for_llm

        # 记录进入 Agent 循环前的列表长度
        original_msg_count = len(messages_for_llm)

        # 在流式推理之前构造 user_msg，确保 created_at 早于所有中间消息
        user_msg = ChatMessage(
            session_id=session_id, role=Role.USER, content=user_query,
            metadata={"states": states} if states else {},
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
                prompt_budget_tokens=prompt_budget_tokens,
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

            if prompt_assembly.precomputed_summary and prompt_assembly.summary_updated_at:
                background_tasks.add_task(
                    self._turn_finalizer.persist_then_apply_compression_result,
                    user_id, session_id, resolved,
                    messages_to_persist,
                    prompt_assembly.precomputed_summary,
                    prompt_assembly.summary_updated_at,
                    prompt_assembly.messages_keep_for_hot_context,
                )
            elif needs_compression:
                background_tasks.add_task(
                    self._turn_finalizer.persist_then_summarize_and_compress,
                    user_id, session_id, resolved,
                    messages_to_persist,
                    messages_keep,
                    messages_compress_candidates,
                    session_summary,
                )
            else:
                background_tasks.add_task(
                    self._turn_finalizer.persist_all,
                    user_id, session_id, resolved,
                    messages_to_persist
                )
            background_tasks.add_task(
                self._turn_finalizer.auto_generate_title,
                session_id, user_id, user_query
            )
