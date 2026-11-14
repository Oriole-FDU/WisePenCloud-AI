import asyncio

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from common.core.domain import R
from common.security import require_login, SecurityContextHolder
from common.logger import error, info
from chat.api.schemas.chat import ActiveChatTurnResponse, ChatCancelRequest, ChatCompactRequest, ChatRedirectRequest, ChatRequest, ChatRecoverRequest, ChatRewriteRequest
from chat.application.agents import AgentResolver
from chat.application.agents.resolver import resolve_session_agent
from chat.application.chat_context_assembler import ChatContextAssembler
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.chat_turn_stream_manager import ChatTurnStreamManager
from chat.application.frontend_tool_response_broker import FrontendToolResponseBroker
from chat.application.tools.core.definition import ClientToolResult, ToolApprovalStatus
from chat.application.tools.client_tools import ClientToolCapability
from chat.container import Container
from chat.domain.repositories import SessionRepository, RedirectQueueRepository, MessageRepository, HotContextRepository
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces.context_compact import ContextCompactor
from common.core.exceptions import ServiceException

router = APIRouter()

_SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "x-vercel-ai-ui-message-stream": "v1",
}


async def _turn_stream_generator(chat_gen):
    """订阅 Redis 中的 turn SSE frame；页面断开不会取消后端 runner。"""
    try:
        async for frame in chat_gen:
            yield frame
    except asyncio.CancelledError:
        info("chat turn SSE subscriber cancelled.")
        return


@router.post(
    "/completions",
    summary="发送流式对话",
    description="""
- 用途：在指定会话中发起一轮 Chat Turn，用于把用户最新输入交给当前会话绑定的 Agent 编排执行，并以流式事件返回模型推理、工具调用和最终回复。
- 请求：session_id 指定目标会话；query 是本轮用户输入；model 指定模型 ID，若会话 Agent 的 model_policy 不允许请求覆盖则改用 Agent 默认模型；provider_id 可选指定该模型的一条 active Provider 映射，未传时选择首选映射；billing_context_group_id 可选指定当前小组场景并优先于用户保存的小组计费顺序；runtime_options 覆盖 Provider manifest 默认运行参数；frontend_states 会筛选未禁用且有值的前端状态写入应用上下文；user_defined_attachment_ids 仅用于标记本轮重点附件，实际可见附件仍来自会话已关联的临时附件和资源附件；tool_selection_default_enabled 与 tool_selection_overrides 控制用户可选工具，Contextual 工具由后端上下文决定且只能被 overrides 中的 false 排除；on-demand skill 参数用于覆盖 Agent 的本轮 Skill 候选。
- 约束：当前用户必须已登录；query 和 session_id 不能为空；目标会话必须属于当前用户；model、provider_id 必须是合法 ObjectId；目标模型必须是 active 的用户模型或系统模型；provider_id 必须属于该模型的 active 映射；Provider 必须 active；runtime_options 必须符合目标 Provider 的 JSON Schema；工具、Skill、记忆和模型覆盖最终受会话 Agent 策略约束。
- 处理：先校验会话归属，再读取会话绑定 Agent，没有绑定时使用默认 Agent；根据 Agent model_policy 解析最终模型、Provider、Provider 侧模型名和运行参数；付费官方 Provider 调用前检查用户级欠费熔断，未被熔断才继续；按 Agent memory_policy 加载 Redis 热上下文，必要时从 MongoDB 回填，按配置召回长期记忆和会话摘要；按工具与 Skill 策略匹配本轮可展示 Skill、派生 ToolScope，并读取会话临时附件和资源附件；组装 system prompt、历史摘要、历史明细、长期记忆、前端状态、Skill metadata、附件清单和用户 query 后进入多步 ReAct 循环；循环中把 Provider 原生流转换为 AI SDK 6.x UIMessage Stream 事件，工具调用会先输出输入事件、并发执行工具，再输出工具结果并继续下一步模型推理；响应返回后先追加 Redis 热上下文并按配置落 MongoDB，随后由 BackgroundTasks 将 Agent 资源、首选小组和 Token 用量发送给用户服务（由用户服务解析最终付款组并结算）、写入长期记忆、压缩摘要和按需自动生成标题。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；query 或 session_id 为空 -> HTTP 400；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；用户个人欠费达到硬阈值 -> ChatErrorCode.CHAT_WALLET_BLOCKED；模型不存在、未启用或不可访问 -> ChatErrorCode.MODEL_NOT_FOUND；模型供应商映射不存在或未启用 -> ChatErrorCode.MODEL_MAPPING_NOT_FOUND；Provider 不存在或未启用 -> ChatErrorCode.PROVIDER_NOT_FOUND；Provider 类型无对应运行时适配器 -> ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED；runtime_options 不符合目标 Provider schema -> ChatErrorCode.MODEL_RUNTIME_OPTIONS_INVALID；上下文超过模型限制 -> ChatErrorCode.CONTEXT_LIMIT_EXCEEDED；大模型或 Provider 流式调用失败 -> ChatErrorCode.LLM_GENERATION_FAILED。
- 响应：返回 text/event-stream，并设置 x-vercel-ai-ui-message-stream=v1；事件使用 AI SDK 6.x UIMessage Stream 语义，后端 runner 会把事件写入 Redis Stream，当前 HTTP 连接只订阅事件流；页面断开不会取消 runner，重新打开页面时应通过 /completions/stream 续接。
""",
)
@inject
async def chat_completions(
        req: ChatRequest,
        user_id: str = Depends(require_login),
        coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
        turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    if not req.query:
        raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID)

    if not req.session_id:
        raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID)

    resolved_model_id = PydanticObjectId(req.model)
    resolved_provider_id = PydanticObjectId(req.provider_id) if req.provider_id else None
    identity_type = SecurityContextHolder.get_identity_type()

    await session_repo.get_session_for_user(req.session_id, user_id)

    turn_id = await turn_stream_manager.start_turn(
        user_id=user_id,
        session_id=req.session_id,
        build_chat_gen=lambda background_tasks, turn_id: coordinator.handle_new_chat_start(
            user_id=user_id,
            session_id=req.session_id,
            user_query=req.query,
            background_tasks=background_tasks,
            model_id=resolved_model_id,
            provider_id=resolved_provider_id,
            billing_context_group_id=req.billing_context_group_id,
            runtime_options=req.runtime_options,
            frontend_states=req.frontend_states,
            user_defined_attachment_ids=req.user_defined_attachment_ids,
            tool_selection_default_enabled=req.tool_selection_default_enabled,
            tool_selection_overrides=req.tool_selection_overrides,
            user_defined_on_demand_skill_ids=req.user_defined_on_demand_skill_ids,
            client_tool_capabilities=[
                ClientToolCapability(
                    name=item.name,
                    description=item.description,
                    input_schema=item.input_schema,
                )
                for item in req.client_tool_capabilities
            ],
            cancel_requested=lambda: turn_stream_manager.is_turn_cancel_requested(turn_id),
            identity_type=identity_type,
        ),
    )

    return StreamingResponse(
        _turn_stream_generator(turn_stream_manager.subscribe_turn(turn_id)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.get(
    "/completions/active",
    response_model=R[ActiveChatTurnResponse],
    summary="查询当前会话正在运行的 Chat Turn",
)
@inject
async def chat_active_turn(
    session_id: str,
    user_id: str = Depends(require_login),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_session_for_user(session_id, user_id)
    turn_id = await turn_stream_manager.active_turn_id(user_id=user_id, session_id=session_id)
    return R.success(data=ActiveChatTurnResponse(turn_id=turn_id))


@router.get("/completions/stream", summary="重连当前会话正在运行的 Chat Turn SSE")
@inject
async def chat_turn_stream(
    session_id: str,
    user_id: str = Depends(require_login),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_session_for_user(session_id, user_id)
    turn_id = await turn_stream_manager.active_turn_id(user_id=user_id, session_id=session_id)
    if turn_id is None:
        raise ServiceException(ChatErrorCode.CHAT_ACTIVE_TURN_NOT_FOUND)

    return StreamingResponse(
        _turn_stream_generator(turn_stream_manager.subscribe_turn(turn_id)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )

def _to_domain_client_results(req: ChatRecoverRequest) -> list[ClientToolResult]:
    return [
        ClientToolResult(
            tool_call_id=item.tool_call_id,
            is_error=item.error_text is not None,
            output=item.error_text if item.error_text is not None else item.output,
        )
        for item in req.client_tool_results
    ]


def _to_domain_approval_status(req: ChatRecoverRequest) -> list[ToolApprovalStatus]:
    return [
        ToolApprovalStatus(tool_call_id=item.tool_call_id, approved=item.approved)
        for item in req.tool_approval_status
    ]


@router.post("/completions/respond", summary="向仍在等待的 Chat Turn 提交前端工具结果或审批状态")
@inject
async def chat_turn_respond(
    req: ChatRecoverRequest,
    user_id: str = Depends(require_login),
    response_broker: FrontendToolResponseBroker = Depends(Provide[Container.frontend_tool_response_broker]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_session_for_user(req.session_id, user_id)
    await response_broker.submit(
        session_id=req.session_id,
        client_tool_results=_to_domain_client_results(req),
        tool_approval_status=_to_domain_approval_status(req),
    )
    return R.success()


@router.post("/completions/recover", summary="提交外部工具结果或审批状态并恢复对话")
@inject
async def chat_recover(
    req: ChatRecoverRequest,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_session_for_user(req.session_id, user_id)
    identity_type = SecurityContextHolder.get_identity_type()

    active_turn_id = await turn_stream_manager.active_turn_id(user_id=user_id, session_id=req.session_id)
    if active_turn_id is not None:
        raise ServiceException(ChatErrorCode.CHAT_TURN_IN_PROGRESS)

    turn_id = await turn_stream_manager.start_turn(
        user_id=user_id,
        session_id=req.session_id,
        build_chat_gen=lambda background_tasks, turn_id: coordinator.handle_suspended_chat_recover(
            user_id=user_id,
            session_id=req.session_id,
            client_tool_results=_to_domain_client_results(req),
            tool_approval_status=_to_domain_approval_status(req),
            background_tasks=background_tasks,
            cancel_requested=lambda: turn_stream_manager.is_turn_cancel_requested(turn_id),
            identity_type=identity_type,
        ),
    )

    return StreamingResponse(
        _turn_stream_generator(turn_stream_manager.subscribe_turn(turn_id)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post("/completions/cancel", summary="取消当前正在运行的 Chat Turn")
@inject
async def chat_cancel(
    req: ChatCancelRequest,
    user_id: str = Depends(require_login),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.get_session_for_user(req.session_id, user_id)
    await turn_stream_manager.cancel_turn(user_id=user_id, session_id=req.session_id)
    return R.success()


@router.post(
    "/completions/rewrite",
    summary="重写指定 user 消息并重新生成后续回复",
    description="""
- 用途：编辑某条历史 user 消息并丢弃其后的所有记录，用新文本作为新的 user 消息重新生成。
- 约束：目标会话必须属于当前用户；当前会话不能有正在运行的 turn；message_id 必须指向该会话中的一条 user 消息。
- 处理：删除该 user 消息及之后的所有消息，重置 Redis 热上下文（下一轮自动按截断后的 Mongo 回填）、清空会话摘要与上下文占用指示，然后以 new_query 开启一轮新的 Chat Turn。
- 失败：会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；有运行中 turn -> ChatErrorCode.CHAT_TURN_IN_PROGRESS；消息不存在 -> ChatErrorCode.CHAT_MESSAGE_NOT_FOUND；目标不是 user 消息 -> ChatErrorCode.CHAT_REWRITE_TARGET_INVALID。
""",
)
@inject
async def chat_rewrite(
    req: ChatRewriteRequest,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
    message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
    hot_context_repo: HotContextRepository = Depends(Provide[Container.hot_context_repo]),
    agent_resolver: AgentResolver = Depends(Provide[Container.agent_resolver]),
):
    if not req.new_query: raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID)
    session = await session_repo.get_session_for_user(req.session_id, user_id)
    if session.is_draft_debug:
        await resolve_session_agent(agent_resolver, session)

    active_turn_id = await turn_stream_manager.active_turn_id(user_id=user_id, session_id=req.session_id)
    if active_turn_id is not None:
        raise ServiceException(ChatErrorCode.CHAT_TURN_IN_PROGRESS)

    # 校验模型参数
    resolved_model_id = PydanticObjectId(req.model)
    resolved_provider_id = PydanticObjectId(req.provider_id) if req.provider_id else None
    identity_type = SecurityContextHolder.get_identity_type()

    # 删除锚点 user 消息及其后的所有消息，并重置热上下文与摘要，再以 new_query 开新一轮
    await message_repo.delete_messages_after_target_user_message(req.session_id, req.message_id)
    # 清空 Redis 热上下文，下一轮 get_chat_history_record_messages 会按截断后的 Mongo 自动回填
    await hot_context_repo.load_messages(req.session_id, [])
    # 摘要可能总结了已删除的消息，第一期直接置空（接受代价）
    await session_repo.update_session_summary(req.session_id, None, None)
    await session_repo.update_context_tokens(req.session_id, 0)

    turn_id = await turn_stream_manager.start_turn(
        user_id=user_id,
        session_id=req.session_id,
        build_chat_gen=lambda background_tasks, turn_id: coordinator.handle_new_chat_start(
            user_id=user_id,
            session_id=req.session_id,
            user_query=req.new_query,
            background_tasks=background_tasks,
            model_id=resolved_model_id,
            provider_id=resolved_provider_id,
            cancel_requested=lambda: turn_stream_manager.is_turn_cancel_requested(turn_id),
            identity_type=identity_type,
        ),
    )

    return StreamingResponse(
        _turn_stream_generator(turn_stream_manager.subscribe_turn(turn_id)),
        media_type="text/event-stream",
        headers=_SSE_HEADERS,
    )


@router.post(
    "/completions/redirect",
    summary="打断当前 turn 并注入新的引导消息",
    description="""
- 用途：在 turn 运行期间用新消息重新引导；引导消息入队，会在当前 ReAct 循环的下一个步骤边界被注入为新的 user 消息，继续循环而不结束 turn。
- 约束：目标会话必须属于当前用户；query 不能为空；当前会话必须存在运行中的 turn，否则拒绝。
- 处理：把 query 入队到 session 的引导队列；运行中的 turn 在步骤边界（工具结果返回后 / 上一步结束时）排空队列并追加为 user 消息继续生成。
- 失败：会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；无运行中 turn -> ChatErrorCode.CHAT_REDIRECT_NO_ACTIVE_TURN。
- 响应：立即返回 ack。
""",
)
@inject
async def chat_redirect(
    req: ChatRedirectRequest,
    user_id: str = Depends(require_login),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    redirect_queue: RedirectQueueRepository = Depends(Provide[Container.chat_turn_queue_repo]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    if not req.query:
        raise ServiceException(ChatErrorCode.CHAT_REQUEST_INVALID)

    await session_repo.get_session_for_user(req.session_id, user_id)

    active_turn_id = await turn_stream_manager.active_turn_id(user_id=user_id, session_id=req.session_id)
    if active_turn_id is None:
        raise ServiceException(ChatErrorCode.CHAT_REDIRECT_NO_ACTIVE_TURN)

    await redirect_queue.enqueue(user_id, req.session_id, req.query)
    return R.success()


@router.post("/completions/compact", summary="立即压缩当前会话上下文")
@inject
async def chat_compact(
    req: ChatCompactRequest,
    user_id: str = Depends(require_login),
    context_compactor: ContextCompactor = Depends(Provide[Container.context_compactor]),
    context_assembler: ChatContextAssembler = Depends(Provide[Container.context_assembler]),
    agent_resolver: AgentResolver = Depends(Provide[Container.agent_resolver]),
    turn_stream_manager: ChatTurnStreamManager = Depends(Provide[Container.chat_turn_stream_manager]),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.get_session_for_user(req.session_id, user_id)

    active_turn_id = await turn_stream_manager.active_turn_id(user_id=user_id, session_id=req.session_id)
    if active_turn_id is not None:
        raise ServiceException(ChatErrorCode.CHAT_TURN_IN_PROGRESS)

    agent = await resolve_session_agent(agent_resolver, session)
    if agent is None:
        raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)

    memory_policy = agent.spec.memory_policy
    if not (memory_policy.enable_chat_memory and memory_policy.enable_chat_memory_summary):
        return R.success()

    history = await context_assembler.get_chat_history_record_messages(req.session_id)
    await context_compactor.compact(
        session_id=req.session_id,
        history=history,
        existing_summary=session.current_summary,
        summary_prompt=memory_policy.summary_prompt,
    )

    # 压缩后上下文已缩小，清空占用指示器，等下一次 LLM 调用重新写入
    try:
        await session_repo.update_context_tokens(req.session_id, 0)
    except Exception as e:
        error("chat context tokens reset failed.", session_id=req.session_id, exc=e)
    return R.success()
