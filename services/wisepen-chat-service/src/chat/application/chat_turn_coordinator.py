from dataclasses import dataclass, field
import asyncio
import uuid
from contextlib import aclosing
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
from chat.core.providers import OssFileLoader
from chat.domain.interfaces.llm import TextCompletionProvider, TokenUsage
from chat.domain.interfaces.memory import MemoryProvider
from chat.domain.repositories import SessionRepository, MessageRepository, HotContextRepository, ModelRepository, \
    ProviderRepository, SuspendedChatRepository
from common.core.exceptions import ServiceException
from chat.application.chat_context_assembler import ChatContextAssembler, WindowedMessages
from chat.application.chat_turn_tool_policy import ChatTurnToolPolicyBuilder
from chat.application.query_loop_runtime import QueryLoopRuntime
from chat.application.execution_bindings import ExecutionBindings
from chat.application.subagents.models import SubagentOptions
from chat.application.subagents.runtime import SubagentRuntime
from chat.application.subagents.tools import SUBAGENT_TOOL_NAMES
from chat.core.persistence.mongo.subagent_repository import MongoSubagentRepository
from chat.core.persistence.redis.chat_turn_stream import RedisChatTurnStream
from chat.application.agents import (
    AgentResolver,
    DefaultAgentResolver, AgentSpec, DEFAULT_AGENT_ID,
)
from chat.application.events import StepFinishEvent, ErrorEvent
from chat.api.vercel_sse_mapper import to_vercel_sse
from chat.application.chat_turn_finalizer import ChatTurnFinalizer
from chat.application.tools.core import ToolRegistry
from chat.application.tools.core.execution.dispatcher import ToolDispatcher
from chat.application.tools.client_tools import ClientToolCapability
from chat.application.tools.core.definition import ClientToolResult, ToolApprovalStatus
from common.kafka.producer import KafkaProducerClient
from common.security import SecurityContextHolder
from chat.service_client import ResourceClient


@dataclass(frozen=False)
class ChatTurnContext:
    user_id: str = None
    session_id: str = None
    model_info: ModelRequestInfo = None
    agent_spec: AgentSpec = None
    session_summary: Optional[str] = None
    windowed_history_messages: WindowedMessages = None
    tool_scope: ToolScope = None
    messages_for_llm: list[ChatMessage] = field(default_factory=list)
    chat_record_messages: list[ChatMessage] = field(default_factory=list)
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    root_execution_id: str | None = None
    turn_id: str | None = None
    usage_managed: bool = False
    subagent_state: dict | None = None
    result_status: str = "completed"
    billing_resource_id: Optional[str] = None
    preferred_billing_group_id: Optional[str] = None
    identity_type: Optional[IdentityType] = None

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
            tool_registry: ToolRegistry,
            tool_dispatcher: ToolDispatcher,
            kafka_producer: KafkaProducerClient,
            tool_policy_builder: ChatTurnToolPolicyBuilder,
            oss_file_loader: OssFileLoader,
            agent_resolver: AgentResolver | None = None,
            redis_client: redis.Redis | None = None,
            subagent_repo: MongoSubagentRepository | None = None,
            turn_stream_repo: RedisChatTurnStream | None = None,
            resource_client: ResourceClient | None = None,
    ):
        self._memory = memory
        self._model_repo = model_repo
        self._session_repo = session_repo
        self._context_assembler = ChatContextAssembler(
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo,
            oss_file_loader=oss_file_loader
        )
        self._tool_registry = tool_registry
        self._query_loop_runtime = QueryLoopRuntime(
            llm_provider_resolver=llm_provider_resolver,
            token_counter=token_counter,
            tool_dispatcher=tool_dispatcher,
        )
        self._turn_finalizer = ChatTurnFinalizer(
            text_llm=text_llm,
            token_counter=token_counter,
            memory=memory,
            message_repo=message_repo, session_repo=session_repo, hot_context_repo=hot_context_repo,
            provider_repo=provider_repo,
            kafka_producer=kafka_producer
        )
        self._tool_policy_builder = tool_policy_builder
        self._agent_resolver = agent_resolver or DefaultAgentResolver()
        self._redis = redis_client or redis.from_url(settings.persistence.redis.url, decode_responses=True)

        self._suspended_chat_repo = suspended_chat_repo
        self._subagent_repo = subagent_repo or MongoSubagentRepository()
        self._turn_stream_repo = turn_stream_repo or RedisChatTurnStream()
        self._resource_client = resource_client

    async def handle_suspended_chat_recover(
            self,
            user_id: str,
            session_id: str,
            client_tool_results: list[ClientToolResult],
            tool_approval_status: List[ToolApprovalStatus],
            background_tasks: BackgroundTasks,
            cancel_requested: Callable[[], Awaitable[bool]] | None = None,
            turn_id: str | None = None,
            lease_lost: Callable[[], bool] | None = None,
            identity_type: Optional[IdentityType] = None,
    ):
        suspended_chat: SuspendedChat | None = await self._suspended_chat_repo.find_suspended_by_session(session_id, user_id)
        if suspended_chat is None:
            raise ServiceException(ChatErrorCode.SUSPENDED_CHAT_NOT_FOUND)
        suspended_chat_id = str(suspended_chat.id)
        state = getattr(suspended_chat.context, "subagent_state", None)
        current_spec = suspended_chat.context.agent_spec
        if state is not None:
            session = await self._session_repo.get_session_for_user(session_id, user_id)
            current_agent = await self._agent_resolver.resolve(session.agent_id, session.agent_version)
            if current_agent is None:
                raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)
            current_spec = current_agent.spec
        tool_scope = await self._tool_registry.recover_derived(
            suspended_chat.context.tool_scope_data,
            user_id,
            enable_use_tool=current_spec.tool_and_skill_policy.enable_use_tool,
        )

        chat_turn_context = ChatTurnContext(
            user_id=user_id,
            session_id=session_id,
            model_info=suspended_chat.context.model_info,
            agent_spec=current_spec,
            session_summary=suspended_chat.context.session_summary,
            windowed_history_messages=suspended_chat.context.windowed_history_messages,
            tool_scope=tool_scope,
            # messages_for_llm 已包含挂起前的 assistant 工具调用，恢复时直接续接该上下文。
            messages_for_llm=list(suspended_chat.context.messages_for_llm),
            # 挂起前的消息和 token 由首次批次处理；恢复批次只记录新增工具结果和回复。
            chat_record_messages=[],
            token_usage=TokenUsage(),
            root_execution_id=getattr(suspended_chat.context, "root_execution_id", None),
            turn_id=turn_id, subagent_state=state,
            billing_resource_id=getattr(suspended_chat.context, "billing_resource_id", None),
            preferred_billing_group_id=getattr(suspended_chat.context, "preferred_billing_group_id", None),
            identity_type=identity_type,
        )
        if state is not None:
            chat_turn_context.model_info = await self._resolve_subagent_model(
                chat_turn_context, chat_turn_context.model_info.model_id, chat_turn_context.model_info.provider_id,
                chat_turn_context.model_info.runtime_options,
            )
        await self._ensure_billable_chat_allowed(
            user_id=user_id,
            model_info=chat_turn_context.model_info,
        )

        async with aclosing(self.query_llm(
            chat_turn_context=chat_turn_context,
            client_tool_results=client_tool_results,
            tool_approval_status=tool_approval_status,
            start_iteration=suspended_chat.context.turn_suspension.iteration,
            bindings=ExecutionBindings(cancel_requested=cancel_requested, lease_lost=lease_lost),
            subagents=SubagentOptions.model_validate(state["options"]) if state is not None else None,
        )) as stream:
            async for event in stream:
                yield event
        await self._persist_message_and_token_bill(chat_turn_context, skip_first_user_message=False)
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
            subagents: SubagentOptions | None = None,
            turn_id: str | None = None,
            lease_lost: Callable[[], bool] | None = None,
            identity_type: Optional[IdentityType] = None,
    ):
        chat_turn_context = ChatTurnContext()
        chat_turn_context.session_id = session_id
        chat_turn_context.user_id = user_id
        chat_turn_context.turn_id = turn_id
        if subagents is not None:
            chat_turn_context.root_execution_id = f"root_{uuid.uuid4().hex}"
        chat_turn_context.identity_type = identity_type
        # 获取当前对话的 Agent
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        if (
            session.agent_id
            and session.agent_id != DEFAULT_AGENT_ID
            and (session.agent_version is None or session.agent_version <= 0)
        ):
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)
        agent = await self._agent_resolver.resolve(session.agent_id, session.agent_version)
        if agent is None:
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)

        chat_turn_context.agent_spec = agent.spec
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
            identity_type=identity_type,
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

        # Token窗口尺寸
        context_limit = chat_turn_context.model_info.context_window_tokens or settings.context.token_limit
        output_reserve = chat_turn_context.model_info.max_output_tokens or settings.context.default_output_reserve_tokens
        prompt_budget_tokens = max(
            context_limit - output_reserve,
            settings.context.min_prompt_budget_tokens,
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

        # 加载会话的历史摘要 (若启用，前提是必须启用会话历史)
        if memory_policy.enable_chat_memory and memory_policy.enable_chat_memory_summary:
            chat_turn_context.session_summary = await self._context_assembler.get_session_summary(session_id)

            # 窗口化消息以用于压缩
            # 从后往前累加 Token，低水位内保留为 messages_keep，更早的未压缩明细进入 messages_compress_candidates
            # candidates 当前轮仍会进入 prompt，本轮结束后会被合并进新摘要，并在下一轮不再作为明细注入
            chat_turn_context.windowed_history_messages = await self._context_assembler.build_windowed_messages(
                chat_history_record_messages,
                prompt_budget_tokens=prompt_budget_tokens,
                high_watermark_ratio=memory_policy.high_watermark_ratio,
                low_watermark_ratio=memory_policy.low_watermark_ratio,
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
        if subagents is not None and tool_and_skill_policy.enable_use_tool:
            tool_policy.expose_tool_name_set.update(SUBAGENT_TOOL_NAMES)

        chat_turn_context.tool_scope = await self._tool_registry.derive(
            tool_context=tool_policy.tool_context,
            expose_tool_name_set=tool_policy.expose_tool_name_set,
            tool_selection_default_enabled=tool_policy.tool_selection_default_enabled,
            tool_selection_overrides=tool_policy.tool_selection_overrides,
            user_id=user_id,
            client_tool_capabilities=client_tool_capabilities,
            enable_use_tool=tool_and_skill_policy.enable_use_tool,
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
        if subagents is not None:
            chat_turn_context.messages_for_llm.insert(1, ChatMessage(
                session_id=session_id, role=Role.SYSTEM,
                content="[Subagent candidates]\n" + subagents.model_dump_json()
                        + "\nDynamic children use instructions; published agents require an exact candidate version.",
            ))

        # 构造 chat_record_messages
        # chat_record_messages 将用于记录本轮对话的历史，以供后续对话使用
        user_message_metadata = {
            "relevant_facts": relevant_facts,
            "frontend_states": frontend_states or {},
            "available_skills_id": [skill.skill_id for skill in available_skills] or [],
            "user_defined_attachment_ids": user_defined_attachment_ids or []
        }
        if chat_turn_context.root_execution_id:
            user_message_metadata["root_execution_id"] = chat_turn_context.root_execution_id
        current_attachment_refs = self._context_assembler.build_message_attachment_refs(
            temp_attachments=temp_attachments,
            resource_attachments=resource_attachments,
            user_defined_attachment_ids=user_defined_attachment_ids,
        )

        chat_turn_context.chat_record_messages = [ChatMessage(
            session_id=session_id, role=Role.USER, content=user_query,
            metadata=user_message_metadata,
            attachments=current_attachment_refs,
        )]
        await self._turn_finalizer.persist_user_message(
            chat_message=chat_turn_context.chat_record_messages[0],
            memory_policy=memory_policy,
        )

        chat_turn_context.token_usage = TokenUsage()
        async with aclosing(self.query_llm(
                chat_turn_context=chat_turn_context,
                client_tool_results=None,
                tool_approval_status=None,
                bindings=ExecutionBindings(cancel_requested=cancel_requested, lease_lost=lease_lost),
                subagents=subagents,
        )) as stream:
            async for event in stream:
                yield event
        await self._persist_message_and_token_bill(chat_turn_context, skip_first_user_message=True)
        self._set_post_completion_tasks(background_tasks, chat_turn_context)

    async def query_llm(
            self, chat_turn_context: ChatTurnContext, client_tool_results, tool_approval_status,
            start_iteration: int = 0, bindings: ExecutionBindings | None = None,
            subagents: SubagentOptions | None = None,
    ):
        bindings = bindings or ExecutionBindings()
        if subagents is None or (chat_turn_context.subagent_state is None
                                and not any(chat_turn_context.tool_scope.get(name) for name in SUBAGENT_TOOL_NAMES)):
            async for frame in self._query_llm(chat_turn_context, client_tool_results, tool_approval_status, start_iteration, bindings):
                yield frame
            return

        frames = asyncio.Queue()
        runtime = SubagentRuntime(options=subagents, context=chat_turn_context, loop=self._query_loop_runtime,
                                  bindings=bindings, emit=frames.put_nowait,
                                  bill=lambda usage, model_info: self._turn_finalizer.send_usage_billing(
                                      user_id=chat_turn_context.user_id, model_info=model_info,
                                      usage=usage, billing_resource_id=chat_turn_context.billing_resource_id,
                                      preferred_billing_group_id=chat_turn_context.preferred_billing_group_id),
                                  billing_multiplier=1 + self._turn_finalizer._service_fee_rate(chat_turn_context.identity_type),
                                  repository=self._subagent_repo, stream_repo=self._turn_stream_repo,
                                  ensure_session=lambda: self._session_repo.get_session_for_user(
                                      chat_turn_context.session_id, chat_turn_context.user_id),
                                  resolve_agent=lambda agent_id, version: self.load_subagent_agent(
                                      chat_turn_context.user_id, agent_id, version),
                                  resolve_model=lambda model, provider_id, runtime_options: self._resolve_subagent_model(
                                      chat_turn_context, model, provider_id, runtime_options))
        await runtime.initialize()
        chat_turn_context.usage_managed = True
        runtime.attach()

        async def produce():
            try:
                async for frame in self._query_llm(chat_turn_context, client_tool_results, tool_approval_status, start_iteration, bindings):
                    frames.put_nowait(frame)
            except Exception:
                chat_turn_context.result_status = "failed"
                raise
            finally:
                try:
                    await runtime.close()
                finally:
                    frames.put_nowait(None)

        producer = asyncio.create_task(produce())
        try:
            while (frame := await frames.get()) is not None:
                yield frame
            await producer
        finally:
            if not producer.done():
                producer.cancel()
            await asyncio.gather(producer, return_exceptions=True)

    async def _query_llm(
            self,
            chat_turn_context: ChatTurnContext,
            client_tool_results: list[ClientToolResult] | None,
            tool_approval_status: List[ToolApprovalStatus] | None,
            start_iteration: int = 0,
            bindings: ExecutionBindings | None = None,
    ):
        # 流式推理
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
                bindings=bindings,
            ):
                # QueryLoopRuntime 产出的事件如果是 StepFinishEvent 额外处理消息累积
                if isinstance(event, StepFinishEvent):
                    if chat_turn_context.root_execution_id:
                        for message in [*event.intermediate_messages, event.final_assistant_message]:
                            if message is not None:
                                message.metadata["root_execution_id"] = chat_turn_context.root_execution_id
                    chat_turn_context.token_usage.add(event.token_usage) # 计费
                    if not event.is_finished:
                        # 向 chat_record_messages 追加中间消息（Tool Calls）
                        # 子任務整合提示只給本輪模型，不能冒充真人訊息進入歷史或長期記憶。
                        chat_turn_context.chat_record_messages.extend(
                            message for message in event.intermediate_messages
                            if not message.metadata.get("subagent_results")
                        )
                        if event.aborted:
                            chat_turn_context.result_status = "cancelled"
                            yield to_vercel_sse(event)
                            yield to_vercel_sse(ErrorEvent(error_text="本轮对话已被用户取消"))
                            return
                        # SSE流需要中断（因调用客户端工具、需要工具调用批准等）
                        if event.suspension is not None:
                            chat_turn_context.messages_for_llm.extend(event.intermediate_messages)
                            if bindings.snapshot_subagents is not None:
                                chat_turn_context.subagent_state = await bindings.snapshot_subagents()
                            suspended_turn_context = SuspendedTurnContext(
                                model_info=chat_turn_context.model_info,
                                agent_spec=chat_turn_context.agent_spec,
                                session_summary=chat_turn_context.session_summary,
                                windowed_history_messages=chat_turn_context.windowed_history_messages,
                                tool_scope_data=chat_turn_context.tool_scope.to_suspension_data(),
                                messages_for_llm=chat_turn_context.messages_for_llm,
                                chat_record_messages=chat_turn_context.chat_record_messages,
                                token_usage=chat_turn_context.token_usage,
                                turn_suspension=event.suspension,
                                root_execution_id=chat_turn_context.root_execution_id,
                                subagent_state=chat_turn_context.subagent_state,
                                billing_resource_id=chat_turn_context.billing_resource_id,
                                preferred_billing_group_id=chat_turn_context.preferred_billing_group_id,
                            )
                            await self._suspended_chat_repo.create(SuspendedChat(
                                session_id=chat_turn_context.session_id,
                                user_id=chat_turn_context.user_id,
                                context=suspended_turn_context
                            ))
                    else:
                        if event.final_assistant_message.metadata.get("incomplete"):
                            chat_turn_context.result_status = "incomplete"
                        # 向 chat_record_messages 追加最终回复消息
                        chat_turn_context.chat_record_messages.append(event.final_assistant_message)
                yield to_vercel_sse(event)

                # 切断SSE流
                if isinstance(event, StepFinishEvent) and event.suspension is not None:
                    return
        except ServiceException as e:
            chat_turn_context.result_status = "failed"
            error("chat stream generation failed.", session_id=chat_turn_context.session_id, exc=e)
            yield to_vercel_sse(ErrorEvent(error_text=str(e)))
            return

    async def _history_policy(self, user_id, session_id):
        session = await self._session_repo.get_session_for_user(session_id, user_id)
        agent = await self._agent_resolver.resolve(session.agent_id, session.agent_version)
        return agent is not None and agent.spec.memory_policy.enable_persistence_chat_memory

    async def _resolve_subagent_model(self, context, model, provider_id, runtime_options):
        info = await self._model_repo.resolve_model_for_chat(
            user_id=context.user_id, model_id=PydanticObjectId(model),
            provider_id=PydanticObjectId(provider_id) if provider_id is not None else None,
            runtime_options=runtime_options,
        )
        await self._ensure_billable_chat_allowed(
            user_id=context.user_id, model_info=info,
        )
        return info

    async def load_subagent_agent(self, user_id, agent_id, version):
        permission = await self._resource_client.check_res_permission(
            resource_id=agent_id, user_id=user_id, group_role_map=SecurityContextHolder.get_group_role_map(),
            target_version=version,
        )
        if not permission or not permission.allows("LOAD"):
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)
        agent = await self._agent_resolver.resolve(agent_id, version)
        if (agent is None or agent.agent_id != agent_id or agent.version != version
                or agent.version_status.upper() != "PUBLISHED" or not agent.spec.system_prompt.strip()):
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)
        return agent

    async def _child_history_allowed(self, user_id, run):
        if not run.history_available:
            return False
        if run.agent_id:
            try:
                agent = await self.load_subagent_agent(user_id, run.agent_id, run.agent_version)
                return agent.spec.memory_policy.enable_persistence_chat_memory
            except Exception:
                return False
        return True

    async def list_subagents(self, *, user_id, session_id, root_execution_id=None):
        allowed = await self._history_policy(user_id, session_id)
        runs = await self._subagent_repo.list_runs(user_id, session_id, root_execution_id)
        await self._reconcile_subagent_runs(user_id, session_id, runs)
        views = []
        for run in runs:
            if not allowed or not await self._child_history_allowed(user_id, run):
                run.history_available = False
                run = run.for_storage()
            views.append(run.public_view())
        root = await self._subagent_repo.get_root(user_id, session_id, root_execution_id) if root_execution_id else None
        return {"runs": views, "usage": [usage.model_dump(mode="json") for usage in root.usage] if root else []}

    async def subagent_detail(self, *, user_id, session_id, child_id, run_id):
        allowed = await self._history_policy(user_id, session_id)
        run = await self._subagent_repo.get_run(user_id, session_id, child_id, run_id)
        if run is not None:
            await self._reconcile_subagent_runs(user_id, session_id, [run])
        if not allowed or run is None or not await self._child_history_allowed(user_id, run):
            raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID, custom_msg="Subagent history is unavailable.")
        from chat.api.converters.ui_message_converter import convert_to_ui_messages
        return {"run": run.public_view(), "messages": convert_to_ui_messages(
            [message for message in run.messages if message.role != Role.SYSTEM],
        )}

    async def _reconcile_subagent_runs(self, user_id, session_id, runs):
        active = await self._turn_stream_repo.get_active_turn(user_id, session_id)
        for run in runs:
            if run.status == "running" and active != run.turn_id:
                run.status = "interrupted"
                run.error = "Previous execution owner is unavailable; external results are unknown. Work was not replayed."
                await self._subagent_repo.save_run(run.for_storage())
                root = await self._subagent_repo.get_root(user_id, session_id, run.root_execution_id)
                if root is not None and root.status == "running" and root.turn_id != active:
                    root.status = "interrupted"
                    await self._subagent_repo.save_root(root)

    async def control_subagent(self, *, user_id, child_id, request, kind):
        await self._session_repo.get_session_for_user(request.session_id, user_id)
        run = await self._subagent_repo.get_run(user_id, request.session_id, child_id, request.run_id)
        if run is None or run.root_execution_id != request.root_execution_id:
            raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID, custom_msg="Subagent is not active in this root.")
        command = {"command_id": request.command_id, "run_id": run.run_id, "kind": kind}
        if kind == "input":
            command["input"] = request.input
        result = await self._turn_stream_repo.submit_subagent_command(
            user_id, request.session_id, run.turn_id, command, allow_new=run.status == "running",
        )
        if result is None:
            raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID, custom_msg="Subagent owner is inactive or command conflicts.")
        return result

    async def _persist_message_and_token_bill(
        self,
        chat_turn_context: ChatTurnContext,
        *,
        skip_first_user_message: bool,
    ) -> None:
        """在流完成事件发送前持久化本轮消息并完成用量记录。"""
        await self._turn_finalizer.persist_message_and_token_bill(
            user_id=chat_turn_context.user_id,
            session_id=chat_turn_context.session_id,
            chat_record_messages=chat_turn_context.chat_record_messages,
            memory_policy=chat_turn_context.agent_spec.memory_policy,
            model_info=chat_turn_context.model_info,
            token_usage=chat_turn_context.token_usage,
            billing_resource_id=chat_turn_context.billing_resource_id,
            preferred_billing_group_id=chat_turn_context.preferred_billing_group_id,
            skip_first_user_message=skip_first_user_message,
            skip_billing=chat_turn_context.usage_managed,
            identity_type=chat_turn_context.identity_type,
        )

    def _set_post_completion_tasks(
        self,
        background_tasks,
        chat_turn_context: ChatTurnContext,
    ) -> None:
        # 摘要和标题不影响答案落库，放到完成事件发送后执行。
        if background_tasks is not None:
            # 调用轻量级模型生成并更新会话的全局摘要
            if (chat_turn_context.agent_spec.memory_policy.enable_chat_memory
                    and chat_turn_context.agent_spec.memory_policy.enable_chat_memory_summary
                    and chat_turn_context.windowed_history_messages is not None
                    and chat_turn_context.windowed_history_messages.needs_compression):
                background_tasks.add_task(
                    self._turn_finalizer.summarize_and_compress,
                    session_id=chat_turn_context.session_id,
                    windowed_history_messages=chat_turn_context.windowed_history_messages,
                    chat_record_messages=chat_turn_context.chat_record_messages,
                    existing_summary=chat_turn_context.session_summary,
                    memory_policy=chat_turn_context.agent_spec.memory_policy,
                )
            # 自动生成标题
            if chat_turn_context.agent_spec.auto_generate_title:
                background_tasks.add_task(
                    self._turn_finalizer.auto_generate_title,
                    session_id=chat_turn_context.session_id,
                    user_id=chat_turn_context.user_id,
                    # chat_record_messages的首条消息即为用户查询
                    user_query=str(chat_turn_context.chat_record_messages[0].content)
                )

    async def close_unfinished_before_start(
        self,
        user_id: str,
        session_id: str,
        identity_type: Optional[IdentityType] = None,
    ) -> None:
        unfinished_chat: SuspendedChat | None = await self._suspended_chat_repo.find_suspended_by_session(session_id, user_id)
        if unfinished_chat is None:
            return # 没有未完成的对话，无需关闭
        unfinished_chat_id = str(unfinished_chat.id)
        subagent_state = getattr(unfinished_chat.context, "subagent_state", None)
        first_new_message = len(unfinished_chat.context.chat_record_messages)

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

        for invocation, message in pending_messages:
            unfinished_chat.context.chat_record_messages.append(
                ChatMessage(
                    session_id=session_id, role=Role.TOOL,
                    tool_call_id=invocation.tool_call_id, tool_name=invocation.tool_name,
                    content=message,
                )
            )

        unfinished_chat.context.chat_record_messages.append(
            ChatMessage(
                session_id=session_id, role=Role.ASSISTANT,
                content="本轮对话已中断，未能生成完整回复",
                metadata={"root_execution_id": unfinished_chat.context.root_execution_id}
                         if getattr(unfinished_chat.context, "root_execution_id", None) else {},
            )
        )

        # 完成后处理
        await self._turn_finalizer.persist_message_and_token_bill(
            user_id=unfinished_chat.user_id,
            session_id=unfinished_chat.session_id,
            chat_record_messages=(unfinished_chat.context.chat_record_messages[first_new_message:]
                                  if subagent_state is not None else unfinished_chat.context.chat_record_messages),
            memory_policy=unfinished_chat.context.agent_spec.memory_policy,
            model_info=unfinished_chat.context.model_info,
            token_usage=unfinished_chat.context.token_usage,
            skip_first_user_message=subagent_state is None,
            skip_billing=subagent_state is not None,
            billing_resource_id=getattr(unfinished_chat.context, "billing_resource_id", None),
            preferred_billing_group_id=getattr(
                unfinished_chat.context,
                "preferred_billing_group_id",
                getattr(unfinished_chat.context, "billing_group_id", None),
            ),
            identity_type=identity_type,
        )
        if subagent_state is not None:
            root = await self._subagent_repo.get_root(
                user_id, session_id, unfinished_chat.context.root_execution_id,
            )
            if root is not None:
                root.status = "interrupted"
                await self._subagent_repo.save_root(root)
        # 调用轻量级模型生成并更新会话的全局摘要
        if (unfinished_chat.context.agent_spec.memory_policy.enable_chat_memory
                and unfinished_chat.context.agent_spec.memory_policy.enable_chat_memory_summary
                and unfinished_chat.context.windowed_history_messages is not None
                and unfinished_chat.context.windowed_history_messages.needs_compression):
            await self._turn_finalizer.summarize_and_compress(
                session_id=unfinished_chat.session_id,
                windowed_history_messages=unfinished_chat.context.windowed_history_messages,
                chat_record_messages=unfinished_chat.context.chat_record_messages,
                existing_summary=unfinished_chat.context.session_summary,
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
