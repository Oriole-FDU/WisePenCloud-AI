import asyncio

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from common.core.domain import R
from common.security import require_login
from common.logger import info
from chat.api.schemas.chat import ActiveChatTurnResponse, ChatCancelRequest, ChatRequest, ChatRecoverRequest
from chat.api.schemas.chat import SubagentControlRequest, SubagentInputRequest
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.application.chat_turn_stream_manager import ChatTurnStreamManager
from chat.application.tools.core.definition import ClientToolResult, ToolApprovalStatus
from chat.application.tools.client_tools import ClientToolCapability
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository
from chat.domain.error_codes import ChatErrorCode
from common.core.exceptions import ServiceException

router = APIRouter()

@router.get("/subagents", summary="查询当前会话的子代理执行树")
@inject
async def subagent_list(
    session_id: str,
    root_execution_id: str | None = None,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
):
    return R.success(data=await coordinator.list_subagents(
        user_id=user_id, session_id=session_id, root_execution_id=root_execution_id,
    ))


@router.get("/subagents/{child_id}/runs/{run_id}", summary="查询可保存的子代理历史")
@inject
async def subagent_detail(
    child_id: str,
    run_id: str,
    session_id: str,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
):
    return R.success(data=await coordinator.subagent_detail(
        user_id=user_id, session_id=session_id, child_id=child_id, run_id=run_id,
    ))

@router.post("/subagents/{child_id}/input", summary="向运行中的子代理追加指令")
@inject
async def subagent_input(
    child_id: str, req: SubagentInputRequest,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
):
    return R.success(data=await coordinator.control_subagent(
        user_id=user_id, child_id=child_id, request=req, kind="input",
    ))


@router.post("/subagents/{child_id}/cancel", summary="取消运行中的子代理")
@inject
async def subagent_cancel(
    child_id: str, req: SubagentControlRequest,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
):
    return R.success(data=await coordinator.control_subagent(
        user_id=user_id, child_id=child_id, request=req, kind="cancel",
    ))

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
- 请求：session_id 指定目标会话；query 是本轮用户输入；model 可选指定模型 ID，未传时使用 DEFAULT_MODEL_ID，若会话 Agent 的 model_policy 不允许请求覆盖则改用 Agent 默认模型；provider_id 可选指定该模型的一条 active Provider 映射，未传时选择首选映射；runtime_options 覆盖 Provider manifest 默认运行参数；frontend_states 会筛选未禁用且有值的前端状态写入应用上下文；user_defined_attachment_ids 仅用于标记本轮重点附件，实际可见附件仍来自会话已关联的临时附件和资源附件；tool_selection_default_enabled 与 tool_selection_overrides 控制用户可选工具，Contextual 工具由后端上下文决定且只能被 overrides 中的 false 排除；on-demand skill 参数用于覆盖 Agent 的本轮 Skill 候选。
- 约束：当前用户必须已登录；query 和 session_id 不能为空；目标会话必须属于当前用户；model、provider_id 必须是合法 ObjectId；目标模型必须是 active 的用户模型或系统模型；provider_id 必须属于该模型的 active 映射；Provider 必须 active；runtime_options 必须符合目标 Provider 的 JSON Schema；工具、Skill、记忆和模型覆盖最终受会话 Agent 策略约束。
- 处理：先校验会话归属，再读取会话绑定 Agent，没有绑定时使用默认 Agent；根据 Agent model_policy 解析最终模型、Provider、Provider 侧模型名和运行参数；按 Agent memory_policy 加载 Redis 热上下文，必要时从 MongoDB 回填，按配置召回长期记忆和会话摘要；按工具与 Skill 策略匹配本轮可展示 Skill、派生 ToolScope，并读取会话临时附件和资源附件；组装 system prompt、历史摘要、历史明细、长期记忆、前端状态、Skill metadata、附件清单和用户 query 后进入多步 ReAct 循环；循环中把 Provider 原生流转换为 AI SDK 6.x UIMessage Stream 事件，工具调用会先输出输入事件、并发执行工具，再输出工具结果并继续下一步模型推理；响应返回后通过 BackgroundTasks 发送 token 计费、追加 Redis 热上下文、按配置落 MongoDB、写入长期记忆、压缩摘要并在需要时自动生成标题。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；query 或 session_id 为空 -> HTTP 400；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；模型不存在、未启用或不可访问 -> ChatErrorCode.MODEL_NOT_FOUND；模型供应商映射不存在或未启用 -> ChatErrorCode.MODEL_MAPPING_NOT_FOUND；Provider 不存在或未启用 -> ChatErrorCode.PROVIDER_NOT_FOUND；Provider 类型无对应运行时适配器 -> ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED；runtime_options 不符合目标 Provider schema -> ChatErrorCode.MODEL_RUNTIME_OPTIONS_INVALID；上下文超过模型限制 -> ChatErrorCode.CONTEXT_LIMIT_EXCEEDED；大模型或 Provider 流式调用失败 -> ChatErrorCode.LLM_GENERATION_FAILED。
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

    resolved_model_id = PydanticObjectId(req.model or settings.DEFAULT_MODEL_ID)
    resolved_provider_id = PydanticObjectId(req.provider_id) if req.provider_id else None

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
            runtime_options=req.runtime_options,
            subagents=req.subagents,
            turn_id=turn_id,
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
            lease_lost=lambda: turn_stream_manager.is_turn_lease_lost(turn_id),
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

    turn_id = await turn_stream_manager.start_turn(
        user_id=user_id,
        session_id=req.session_id,
        build_chat_gen=lambda background_tasks, turn_id: coordinator.handle_suspended_chat_recover(
            user_id=user_id,
            session_id=req.session_id,
            client_tool_results=[
                ClientToolResult(
                    tool_call_id=item.tool_call_id,
                    is_error=item.error_text is not None,
                    output=item.error_text if item.error_text is not None else item.output,
                )
                for item in req.client_tool_results
            ],
            tool_approval_status=[
                ToolApprovalStatus(
                    tool_call_id=item.tool_call_id,
                    approved=item.approved,
                )
                for item in req.tool_approval_status
            ],
            background_tasks=background_tasks,
            cancel_requested=lambda: turn_stream_manager.is_turn_cancel_requested(turn_id),
            turn_id=turn_id,
            lease_lost=lambda: turn_stream_manager.is_turn_lease_lost(turn_id),
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
