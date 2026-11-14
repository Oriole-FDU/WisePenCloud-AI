import asyncio
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Set, Awaitable, Callable

from beanie import PydanticObjectId
from fastapi import BackgroundTasks
import redis.asyncio as redis

from chat.application.tools import ToolScope
from chat.domain.entities.suspended_chat import SuspendedTurnContext, SuspendedChat
from common.core.domain import IdentityType
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories.model_repo import ModelRequestInfo
from common.logger import error

from chat.core.config.app_settings import settings
from chat.domain.entities import ChatMessage, Role
from chat.application.llm_provider_resolver import LLMProviderResolver
from chat.application.token_counter import TokenCounter
from chat.domain.interfaces.context_compact import ContextCompactor
from chat.domain.interfaces.llm import TextCompletionProvider, TokenUsage
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import SessionRepository, MessageRepository, HotContextRepository, ModelRepository, \
    ProviderRepository, SuspendedChatRepository, RedirectQueueRepository
from common.core.exceptions import ServiceException
from chat.application.chat_context_assembler import ChatContextAssembler
from chat.application.chat_turn_tool_policy import ChatTurnToolPolicyBuilder
from chat.application.query_loop_runtime import QueryLoopRuntime
from chat.application.agents import (
    AgentResolver,
    DefaultAgentResolver, AgentSpec, DEFAULT_AGENT_ID,
)
from chat.application.agents.resolver import resolve_session_agent
from chat.application.events import ErrorEvent, StepFinishEvent
from chat.api.vercel_sse_mapper import to_vercel_sse
from chat.application.chat_turn_finalizer import ChatTurnFinalizer
from chat.application.frontend_tool_response_broker import FrontendToolResponseBroker
from chat.application.tools.core import ToolRegistry
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.client_tools import ClientToolCapability
from chat.application.tools.core.definition import ClientToolResult, ToolApprovalStatus
from common.kafka.producer import KafkaProducerClient


@dataclass(frozen=False)
class ChatTurnContext:
    user_id: str = None
    session_id: str = None
    model_info: ModelRequestInfo = None
    agent_spec: AgentSpec = None
    session_summary: Optional[str] = None
    tool_scope: ToolScope = None
    messages_for_llm: list[ChatMessage] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    billing_resource_id: Optional[str] = None
    preferred_billing_group_id: Optional[str] = None
    identity_type: Optional[IdentityType] = None
    user_message: ChatMessage | None = None
    final_assistant_message: ChatMessage | None = None

class ChatTurnCoordinator:
    """
    Chat协调器：负责编排聊天流程中的各个环节，包含上下文管理、LLM ReAct、记忆更新等。
    公共入口 handle_start 方法实现了从接收用户输入到生成响应的完整流程，支持异步流式输出和后置处理任务
    """

    def __init__(
            self,
            llm_provider_resolver: LLMProviderResolver,
            text_llm: TextCompletionProvider,
            token_counter: TokenCounter,
            memory: MemoryProvider,
            model_repo: ModelRepository,
            provider_repo: ProviderRepository,
            session_repo: SessionRepository,
            message_repo: MessageRepository,
            hot_context_repo: HotContextRepository,
            suspended_chat_repo: SuspendedChatRepository,
            redirect_queue: RedirectQueueRepository,
            tool_registry: ToolRegistry,
            tool_dispatcher: ToolDispatcher,
            kafka_producer: KafkaProducerClient,
            tool_policy_builder: ChatTurnToolPolicyBuilder,
            context_assembler: ChatContextAssembler,
            frontend_tool_response_broker: FrontendToolResponseBroker,
            context_compactor: ContextCompactor,
            agent_resolver: AgentResolver | None = None,
            redis_client: redis.Redis | None = None,
    ):
        self._memory = memory
        self._model_repo = model_repo
        self._session_repo = session_repo
        self._context_assembler = context_assembler
        self._tool_registry = tool_registry
        self._query_loop_runtime = QueryLoopRuntime(
            llm_provider_resolver=llm_provider_resolver,
            token_counter=token_counter,
            tool_dispatcher=tool_dispatcher,
            frontend_tool_response_broker=frontend_tool_response_broker,
        )
        self._turn_finalizer = ChatTurnFinalizer(
            text_llm=text_llm,
            token_counter=token_counter,
            memory=memory,
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo,
            provider_repo=provider_repo,
            kafka_producer=kafka_producer
        )
        self._context_compactor = context_compactor
        self._tool_policy_builder = tool_policy_builder
        self._agent_resolver = agent_resolver or DefaultAgentResolver()
        self._redis = redis_client or redis.from_url(settings.persistence.redis.url, decode_responses=True)

        self._suspended_chat_repo = suspended_chat_repo
        self._redirect_queue = redirect_queue

    async def handle_suspended_chat_recover(
            self,
            user_id: str,
            session_id: str,
            client_tool_results: list[ClientToolResult],
            tool_approval_status: List[ToolApprovalStatus],
            background_tasks: BackgroundTasks,
            cancel_requested: Callable[[], Awaitable[bool]] | None = None,
            identity_type: Optional[IdentityType] = None,
    ):
        suspended_chat: SuspendedChat | None = await self._suspended_chat_repo.find_suspended_by_session(session_id, user_id)
        if suspended_chat is None:
            raise ServiceException(ChatErrorCode.SUSPENDED_CHAT_NOT_FOUND)
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        if session.is_draft_debug:
            await resolve_session_agent(self._agent_resolver, session)
        suspended_chat_id = str(suspended_chat.id)
        tool_scope = await self._tool_registry.recover_derived(suspended_chat.context.tool_scope_data, user_id)

        chat_turn_context = ChatTurnContext(
            user_id=user_id,
            session_id=session_id,
            model_info=suspended_chat.context.model_info,
            agent_spec=suspended_chat.context.agent_spec,
            session_summary=suspended_chat.context.session_summary,
            tool_scope=tool_scope,
            # messages_for_llm 已包含挂起前的 assistant 工具调用，恢复时直接续接该上下文
            messages_for_llm=list(suspended_chat.context.messages_for_llm),
            # 用户消息
            user_message=suspended_chat.context.user_message,
            # 挂起前的 token 已由首次批次计费，恢复批次只累计新增用量
            token_usage=TokenUsage(),
            billing_resource_id=getattr(suspended_chat.context, "billing_resource_id", None),
            preferred_billing_group_id=getattr(suspended_chat.context, "preferred_billing_group_id", None),
            identity_type=identity_type,
        )
        await self._ensure_billable_chat_allowed(
            user_id=user_id,
            model_info=chat_turn_context.model_info,
        )

        async for event in self.query_llm(
            chat_turn_context=chat_turn_context,
            client_tool_results=client_tool_results,
            tool_approval_status=tool_approval_status,
            start_iteration=suspended_chat.context.turn_suspension.iteration,
            cancel_requested=cancel_requested,
        ):
            yield event
        self._set_post_completion_tasks(background_tasks, chat_turn_context)

        await self._suspended_chat_repo.delete_by_id(suspended_chat_id)

    # -------------------------------------------------------------------------
    # 公共入口
    # -------------------------------------------------------------------------
    async def handle_new_chat_start(
            self,
            user_id: str,
            session_id: str,
            user_query: str,
            background_tasks: BackgroundTasks,
            model_id: PydanticObjectId,
            provider_id: Optional[PydanticObjectId] = None,
            billing_context_group_id: Optional[str] = None,
            runtime_options: dict = None,
            frontend_states: Optional[List[Dict[str, Any]]] = None,
            user_defined_attachment_ids: Optional[List[str]] = None,
            tool_selection_default_enabled: Optional[bool] = None,
            tool_selection_overrides: Optional[Dict[str, bool]] = None,
            user_defined_on_demand_skill_ids: Optional[Set[str]] = None,
            client_tool_capabilities: list[ClientToolCapability] | None = None,
            cancel_requested: Callable[[], Awaitable[bool]] | None = None,
            identity_type: Optional[IdentityType] = None,
    ):
        chat_turn_context = ChatTurnContext()
        chat_turn_context.session_id = session_id
        chat_turn_context.user_id = user_id
        chat_turn_context.identity_type = identity_type
        # 获取当前对话的 Agent
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        if (
            not session.is_draft_debug
            and session.agent_id
            and session.agent_id != DEFAULT_AGENT_ID
            and (session.agent_version is None or session.agent_version <= 0)
        ):
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)
        agent = await resolve_session_agent(self._agent_resolver, session)
        if agent is None:
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)

        chat_turn_context.agent_spec = agent.spec
        if session.is_draft_debug:
            memory_policy = agent.spec.memory_policy.model_copy(update={"enable_long_term_memory": False})
            chat_turn_context.agent_spec = agent.spec.model_copy(update={"memory_policy": memory_policy})
        if (
            user_defined_on_demand_skill_ids is not None
            or tool_selection_default_enabled is not None
            or tool_selection_overrides is not None
        ):
            await self._session_repo.set_session_capability_preferences(
                session_id=session_id,
                user_id=user_id,
                last_selected_skill_ids=(
                    sorted(user_defined_on_demand_skill_ids)
                    if user_defined_on_demand_skill_ids is not None
                    else None
                ),
                last_tool_selection_default_enabled=tool_selection_default_enabled,
                last_tool_selection_overrides=tool_selection_overrides,
            )
        memory_policy = chat_turn_context.agent_spec.memory_policy
        tool_and_skill_policy = chat_turn_context.agent_spec.tool_and_skill_policy
        model_policy = chat_turn_context.agent_spec.model_policy

        # 关闭此前未完成的 SuspendedChat
        await self.close_unfinished_before_start(
            user_id=user_id,
            session_id=session_id,
        )

        # 如果禁止覆盖，且指定了模型和供应商
        if not model_policy.allow_request_override:
            if model_policy.default_model_id: model_id = PydanticObjectId(model_policy.default_model_id)
            if model_policy.default_provider_id: provider_id = PydanticObjectId(model_policy.default_provider_id)

        # 解析模型、映射、供应商和 API 凭证
        chat_turn_context.model_info = await self._model_repo.resolve_model_for_chat(
            model_id=model_id,
            user_id=user_id,
            provider_id=provider_id,
            runtime_options=runtime_options or {}
        )
        chat_turn_context.billing_resource_id = (
            session.agent_id if session.agent_id and session.agent_id != DEFAULT_AGENT_ID else None
        )
        chat_turn_context.preferred_billing_group_id = billing_context_group_id
        await self._ensure_billable_chat_allowed(
            user_id=user_id,
            model_info=chat_turn_context.model_info,
        )

        # 加载会话历史 (若启用)
        # 从 Redis 读取最近对话, 如果 Redis 缓存失效，会自动从 MongoDB 回填最近的 N 条历史，确保对话连贯性
        if memory_policy.enable_chat_memory:
            chat_history_record_messages = await self._context_assembler.get_chat_history_record_messages(session_id)
        else:
            chat_history_record_messages = []

        # 加载长期记忆 (若启用)
        # 从 Memory 按相似度阈值召回跨会话事实 (此处实现是Mem0)
        relevant_facts = []
        if memory_policy.enable_long_term_memory:
            relevant_facts = await self._memory.search(
                user_id=user_id,
                query=user_query,
                limit=memory_policy.long_term_memory_limit,
                score_threshold=memory_policy.long_term_memory_score_threshold,
            )

        # Token窗口尺寸
        context_limit = chat_turn_context.model_info.context_window_tokens or settings.context.token_limit

        # 加载会话的历史摘要 (若启用，前提是必须启用会话历史)
        if memory_policy.enable_chat_memory and memory_policy.enable_chat_memory_summary:
            existing_summary = await self._context_assembler.get_session_summary(session_id)

            # 接近高水位时把最老历史并入摘要，Redis 只保留低水位内的近期明细；未达水位原样返回
            chat_history_record_messages, chat_turn_context.session_summary = await self._context_compactor.auto_compact(
                session_id=session_id,
                history=chat_history_record_messages,
                existing_summary=existing_summary,
                context_tokens=session.context_tokens,
                context_window_tokens=context_limit,
                high_watermark_ratio=memory_policy.high_watermark_ratio,
                low_watermark_ratio=memory_policy.low_watermark_ratio,
                summary_prompt=memory_policy.summary_prompt,
            )

        temp_attachments, resource_attachments = await self._session_repo.get_session_attachments(session_id, user_id)

        # 构建本轮可用的工具和Skill

        has_session_summary = chat_turn_context.session_summary is not None
        agent_on_demand_skill_ids = chat_turn_context.agent_spec.tool_and_skill_policy.on_demand_skill_ids
        if user_defined_on_demand_skill_ids is None: user_defined_on_demand_skill_ids = set()
        if agent_on_demand_skill_ids is None: agent_on_demand_skill_ids = set()

        tool_policy = await self._tool_policy_builder.build(
            user_id=user_id,
            session_id=session_id,
            tool_and_skill_policy=tool_and_skill_policy,
            user_query=user_query,
            frontend_states=frontend_states,
            chat_history_record_messages=chat_history_record_messages,
            has_session_summary=has_session_summary,
            temporary_attachment_refs=temp_attachments,
            tool_selection_default_enabled=tool_selection_default_enabled,
            tool_selection_overrides=tool_selection_overrides,
            user_defined_on_demand_skill_ids=user_defined_on_demand_skill_ids | agent_on_demand_skill_ids,
        )
        available_skills = tool_policy.available_skills

        chat_turn_context.tool_scope = await self._tool_registry.derive(
            tool_context=tool_policy.tool_context,
            expose_tool_name_set=tool_policy.expose_tool_name_set,
            tool_selection_default_enabled=tool_policy.tool_selection_default_enabled,
            tool_selection_overrides=tool_policy.tool_selection_overrides,
            user_id=user_id,
            client_tool_capabilities=client_tool_capabilities,
        )

        # 提示词组装
        # 将系统提示词、Mem0 检索到的事实、会话的历史摘要、前端上下文以及窗口内的未压缩明细消息组装成 LLM 所需的格式
        chat_turn_context.messages_for_llm = await self._context_assembler.assemble_prompt(
            session_id=session_id,
            user_query=user_query,
            system_prompt=chat_turn_context.agent_spec.system_prompt,  # 系统提示词
            session_summary=chat_turn_context.session_summary,  # 会话的历史摘要
            history_messages=chat_history_record_messages, # 会话历史
            relevant_facts=relevant_facts, # 长期记忆检索的事实
            frontend_states=frontend_states, # 用户前端状态
            available_skills=available_skills or None, # 可用技能
            temp_attachments=temp_attachments, # 对话中的全部临时附件
            resource_attachments=resource_attachments, # 对话中的全部资源附件
            user_defined_attachment_ids=user_defined_attachment_ids, # 用户指定的附件
            support_vision=chat_turn_context.model_info.model.support_vision,
        )

        # 构造并持久化用户消息
        # 该消息也作为长期记忆摄入与标题生成的输入
        user_message_metadata = {
            "relevant_facts": relevant_facts,
            "frontend_states": frontend_states or {},
            "available_skills_id": [skill.skill_id for skill in available_skills] or [],
            "user_defined_attachment_ids": user_defined_attachment_ids or []
        }
        current_attachment_refs = self._context_assembler.build_message_attachment_refs(
            temp_attachments=temp_attachments,
            resource_attachments=resource_attachments,
            user_defined_attachment_ids=user_defined_attachment_ids,
        )

        user_message = ChatMessage(
            session_id=session_id, role=Role.USER, content=user_query,
            metadata=user_message_metadata,
            attachments=current_attachment_refs,
        )
        await self._turn_finalizer.persist_user_message(
            chat_message=user_message,
            memory_policy=memory_policy,
        )
        chat_turn_context.user_message = user_message
        chat_turn_context.token_usage = TokenUsage()
        async for event in self.query_llm(
                chat_turn_context=chat_turn_context,
                client_tool_results=None,
                tool_approval_status=None,
                cancel_requested=cancel_requested,
        ):
            yield event
        self._set_post_completion_tasks(background_tasks, chat_turn_context)

    async def query_llm(
            self,
            chat_turn_context: ChatTurnContext,
            client_tool_results: list[ClientToolResult] | None,
            tool_approval_status: List[ToolApprovalStatus] | None,
            start_iteration: int = 0,
            cancel_requested: Callable[[], Awaitable[bool]] | None = None,
    ):
        # 流式推理

        # 落盘与指示器写入按顺序串行后台执行，不阻塞 SSE 流
        persist_lock = asyncio.Lock()
        persist_tasks: list[asyncio.Task] = []

        def schedule(coro: Awaitable[None]) -> None:
            async def _run() -> None:
                async with persist_lock: await coro
            persist_tasks.append(asyncio.create_task(_run()))

        async def drain_pending_user_messages() -> list[ChatMessage]:
            # 检查是否有积压的用户追加消息
            queries = await self._redirect_queue.drain_all(
                chat_turn_context.user_id, chat_turn_context.session_id,
            )
            if not queries: return []
            # 包装为用户消息
            messages = [
                ChatMessage(session_id=chat_turn_context.session_id, role=Role.USER, content=query)
                for query in queries
            ]
            # 持久化用户追加消息
            schedule(self._turn_finalizer.persist_message_delta(
                session_id=chat_turn_context.session_id,
                delta_messages=messages,
                memory_policy=chat_turn_context.agent_spec.memory_policy,
            ))
            return messages

        try:
            async for event in self._query_loop_runtime.stream_chat_with_tool_calling(
                messages=chat_turn_context.messages_for_llm,
                tool_scope=chat_turn_context.tool_scope,
                session_id=chat_turn_context.session_id,
                agent_max_iterations=chat_turn_context.agent_spec.agent_max_iterations,
                model_info=chat_turn_context.model_info,
                start_iteration=start_iteration,
                client_tool_results=client_tool_results,
                tool_approval_status=tool_approval_status,
                cancel_requested=cancel_requested,
                pending_user_messages=drain_pending_user_messages,
            ):
                # QueryLoopRuntime 产出的事件如果是 StepFinishEvent 额外处理消息累积
                if isinstance(event, StepFinishEvent):
                    chat_turn_context.token_usage.add(event.token_usage) # 计费
                    # 上下文指示器：记录最近一次 LLM 调用的输入 token 数
                    if event.token_usage.input_tokens > 0:
                        schedule(self._turn_finalizer.update_context_tokens(chat_turn_context.session_id, event.token_usage.input_tokens))
                    if not event.is_finished:
                        # 中间消息（Tool Calls）逐步落盘
                        schedule(self._turn_finalizer.persist_message_delta(
                            session_id=chat_turn_context.session_id,
                            delta_messages=event.intermediate_messages,
                            memory_policy=chat_turn_context.agent_spec.memory_policy,
                        ))
                        if event.aborted:
                            yield to_vercel_sse(event)
                            yield to_vercel_sse(ErrorEvent(error_text="本轮对话已被用户取消"))
                            return
                        # 只有等待前端工具结果超时才会挂起
                        if event.suspension is not None:
                            chat_turn_context.messages_for_llm.extend(event.intermediate_messages)
                            suspended_turn_context = SuspendedTurnContext(
                                model_info=chat_turn_context.model_info,
                                agent_spec=chat_turn_context.agent_spec,
                                session_summary=chat_turn_context.session_summary,
                                tool_scope_data=chat_turn_context.tool_scope.to_suspension_data(),
                                messages_for_llm=chat_turn_context.messages_for_llm,
                                user_message=chat_turn_context.user_message,
                                token_usage=chat_turn_context.token_usage,
                                billing_resource_id=chat_turn_context.billing_resource_id,
                                preferred_billing_group_id=chat_turn_context.preferred_billing_group_id,
                                turn_suspension=event.suspension)
                            await self._suspended_chat_repo.create(SuspendedChat(
                                session_id=chat_turn_context.session_id,
                                user_id=chat_turn_context.user_id,
                                context=suspended_turn_context
                            ))
                    else:
                        # 记录最终回复并落盘
                        chat_turn_context.final_assistant_message = event.final_assistant_message
                        if event.final_assistant_message is not None:
                            schedule(self._turn_finalizer.persist_message_delta(
                                session_id=chat_turn_context.session_id,
                                delta_messages=[event.final_assistant_message],
                                memory_policy=chat_turn_context.agent_spec.memory_policy,
                            ))
                yield to_vercel_sse(event)

                # 超时挂起时切断 SSE
                if isinstance(event, StepFinishEvent) and event.suspension is not None:
                    return
        except ServiceException as e:
            error("chat stream generation failed.", session_id=chat_turn_context.session_id, exc=e)
            yield to_vercel_sse(ErrorEvent(error_text=str(e)))
            return
        finally:
            if persist_tasks:
                await asyncio.gather(*persist_tasks, return_exceptions=True)

    def _set_post_completion_tasks(
        self,
        background_tasks,
        chat_turn_context: ChatTurnContext,
    ) -> None:
        # 长期记忆、计费和标题都不影响下一轮对话，因此可延后执行
        # 计费
        background_tasks.add_task(
            self._turn_finalizer.send_token_billing,
            user_id=chat_turn_context.user_id,
            model_info=chat_turn_context.model_info,
            token_usage=chat_turn_context.token_usage,
            billing_resource_id=chat_turn_context.billing_resource_id,
            preferred_billing_group_id=chat_turn_context.preferred_billing_group_id,
            identity_type=chat_turn_context.identity_type,
        )
        # 用户提问 + 最终回复成对摄入长期记忆
        if (chat_turn_context.user_message is not None
                and chat_turn_context.final_assistant_message is not None):
            background_tasks.add_task(
                self._turn_finalizer.ingest_long_term_memory,
                user_id=chat_turn_context.user_id,
                messages=[chat_turn_context.user_message, chat_turn_context.final_assistant_message],
                memory_policy=chat_turn_context.agent_spec.memory_policy,
            )
        # 自动生成标题
        if (chat_turn_context.user_message is not None
                and chat_turn_context.agent_spec.auto_generate_title):
            background_tasks.add_task(
                self._turn_finalizer.auto_generate_title,
                session_id=chat_turn_context.session_id,
                user_id=chat_turn_context.user_id,
                user_query=str(chat_turn_context.user_message.content)
            )

    async def close_unfinished_before_start(
        self,
        user_id: str,
        session_id: str,
    ) -> None:
        unfinished_chat: SuspendedChat | None = await self._suspended_chat_repo.find_suspended_by_session(session_id, user_id)
        if unfinished_chat is None:
            return # 没有未完成的对话，无需关闭
        unfinished_chat_id = str(unfinished_chat.id)

        pending_messages = []
        pending_messages.extend(
            (invocation, "[Tool Approval Interrupted] User did not complete high-risk tool approval before the turn was interrupted.",)
            for invocation in unfinished_chat.context.turn_suspension.classified_tool_invocation_plan.approval_required_tools
        )
        pending_messages.extend(
            (invocation, "[Client Tool Error] Client tool execution was interrupted before it started.",)
            for invocation in unfinished_chat.context.turn_suspension.classified_tool_invocation_plan.client_tools
        )
        pending_messages.extend(
            (invocation, "[Tool Execution Error] Tool execution was interrupted before it started.",)
            for invocation in unfinished_chat.context.turn_suspension.classified_tool_invocation_plan.server_tools
        )

        # 挂起前消息已在每步边界落盘，且挂起时已计费/记忆/标题，这里只落盘中断标记
        closing_messages: List[ChatMessage] = []
        for invocation, message in pending_messages:
            closing_messages.append(
                ChatMessage(
                    session_id=session_id, role=Role.TOOL,
                    tool_call_id=invocation.tool_call_id, tool_name=invocation.tool_name,
                    content=message,
                )
            )

        closing_messages.append(
            ChatMessage(
                session_id=session_id, role=Role.ASSISTANT,
                content="本轮对话已中断，未能生成完整回复",
            )
        )

        await self._turn_finalizer.persist_message_delta(
            session_id=unfinished_chat.session_id,
            delta_messages=closing_messages,
            memory_policy=unfinished_chat.context.agent_spec.memory_policy,
        )

        await self._suspended_chat_repo.delete_by_id(unfinished_chat_id)

    async def _ensure_billable_chat_allowed(
        self,
        *,
        user_id: str,
        model_info: ModelRequestInfo,
    ) -> None:
        if not model_info.is_billable:
            return
        if await self._redis.exists(f"wisepen:chat:block:user:{user_id}") > 0:
            raise ServiceException(ChatErrorCode.CHAT_WALLET_BLOCKED)
