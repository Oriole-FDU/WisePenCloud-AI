from fastapi import APIRouter, Depends, Query
from dependency_injector.wiring import inject, Provide

from chat.api.schemas.session import (
    SessionResponse, CreateSessionRequest, RenameSessionRequest,
    PinSessionRequest, SetSessionAgentRequest, UIMessageResponse,
)
from chat.api.converters import convert_to_ui_messages
from chat.application.agents import AgentResolver
from chat.domain.entities import ChatSession
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import SessionRepository, MessageRepository
from chat.container import Container

from common.security import require_login
from common.core.domain import R, PageResult
from common.core.exceptions import ServiceException

router = APIRouter()


@router.get(
    "/getSession",
    response_model=R[SessionResponse],
    summary="获取会话详情",
    description="""
- 用途：获取当前用户的单个聊天会话详情，用于进入会话或刷新会话状态。
- 请求：session_id 指定目标会话。
- 约束：当前用户必须已登录；目标会话必须属于当前用户。
- 处理：按当前用户和会话 ID 查询会话，并组装基础信息、未删除的临时附件、资源附件和绑定 Agent 信息；不读取历史消息。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND。
- 响应：返回会话详情。
""",
)
@inject
async def get_session(
        session_id: str,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.get_session_for_user(session_id, user_id)
    return R.success(data=SessionResponse.from_entity(session))


@router.post(
    "/createSession",
    response_model=R[SessionResponse],
    status_code=200,
    summary="创建会话",
    description="""
- 用途：为当前用户创建一个新的聊天会话。
- 请求：title 可选指定会话标题，未传时使用默认标题；agent_id 可选指定要绑定的已发布 Agent。
- 约束：当前用户必须已登录；agent_id 非空时必须能解析到可用 Agent。
- 处理：创建会话主记录；当 agent_id 可用时写入 Agent ID 和版本；不创建任何初始消息或附件。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；Agent 不存在或未发布 -> ChatErrorCode.AGENT_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回新建会话信息。
""",
)
@inject
async def create_session(
        req: CreateSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        agent_resolver: AgentResolver = Depends(Provide[Container.agent_resolver]),
):
    session = ChatSession(user_id=user_id, title=req.title or "New Chat")
    if req.agent_id:
        agent = await agent_resolver.resolve(req.agent_id)
        if agent is None:
            raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)
        session.agent_id = agent.agent_id
        session.agent_version = agent.version
    created = await session_repo.create_session(session)
    return R.success(data=SessionResponse.from_entity(created))


@router.get(
    "/listSessions",
    response_model=R[PageResult[SessionResponse]],
    summary="分页查询会话",
    description="""
- 用途：分页查询当前用户的聊天会话列表。
- 请求：page 从 1 开始；size 为每页条数，最大 100。
- 约束：当前用户必须已登录；分页参数必须满足范围约束。
- 处理：按当前用户分页读取会话并转换为列表展示信息；不读取每个会话的历史消息正文。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回分页会话列表、总数、页码、页大小和总页数。
""",
)
@inject
async def list_sessions(
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        size: int = Query(default=20, ge=1, le=100, description="每页条数"),
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    sessions, total = await session_repo.list_sessions_for_user(user_id, page=page, size=size)
    return R.success(data=PageResult.of(
        items=[SessionResponse.from_entity(s) for s in sessions],
        total=total, page=page, size=size,
    ))


@router.post(
    "/deleteSession",
    response_model=R,
    status_code=200,
    summary="删除会话",
    description="""
- 用途：删除当前用户的指定聊天会话。
- 请求：session_id 指定目标会话。
- 约束：当前用户必须已登录；目标会话必须属于当前用户。
- 处理：删除或标记删除目标会话；不返回会话详情。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_session(
        session_id: str,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.delete_session(session_id, user_id)
    return R.success()


@router.get(
    "/listHistoryMessages",
    response_model=R[PageResult[UIMessageResponse]],
    summary="分页查询历史消息",
    description="""
- 用途：分页查询会话历史消息并转换为前端 AI SDK UIMessage 格式。
- 请求：session_id 指定目标会话；page 从 1 开始且 page=1 表示最新回合；size 为每页回合数。
- 约束：当前用户必须已登录；目标会话必须属于当前用户；分页参数必须满足范围约束。
- 处理：先校验会话归属，再按回合分页读取消息并转换为 UIMessage；不修改会话或消息状态。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回分页 UIMessage 列表、总回合数、页码、页大小和总页数。
""",
)
@inject
async def get_session_messages(
        session_id: str,
        page: int = Query(default=1, ge=1, description="页码，从 1 开始（page=1 为最新回合）"),
        size: int = Query(default=20, ge=1, le=100, description="每页回合数"),
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
):
    session = await session_repo.get_session_for_user(session_id, user_id)

    page_messages, total_turns = await message_repo.list_session_message_turns_page(session_id, page=page, size=size)
    available_attachment_ids = {
        ref.attachment_id
        for ref in [*session.temporary_attachment_refs, *session.resource_attachment_refs]
        if not ref.deleted
    }
    ui_messages = convert_to_ui_messages(
        page_messages,
        available_attachment_ids=available_attachment_ids,
    )

    return R.success(data=PageResult.of(
        items=ui_messages,
        total=total_turns, page=page, size=size,
    ))


@router.post(
    "/renameSession",
    response_model=R[SessionResponse],
    status_code=200,
    summary="重命名会话",
    description="""
- 用途：维护会话列表和详情中的展示标题。
- 请求：session_id 指定目标会话；new_title 是新的会话标题，未传或为空时使用默认标题。
- 约束：当前用户必须已登录；目标会话必须属于当前用户。
- 处理：更新会话标题和更新时间；不修改会话消息、附件或绑定 Agent。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回更新后的会话信息。
""",
)
@inject
async def rename_session(
        session_id: str,
        req: RenameSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.rename_session(session_id, user_id, req.new_title or "New Chat")
    return R.success(data=SessionResponse.from_entity(session))


@router.post(
    "/pinSession",
    response_model=R[SessionResponse],
    status_code=200,
    summary="设置会话置顶",
    description="""
- 用途：设置或取消当前用户会话的置顶状态。
- 请求：session_id 指定目标会话；set_pin 表示是否置顶。
- 约束：当前用户必须已登录；目标会话必须属于当前用户。
- 处理：更新会话置顶标记和更新时间；不修改会话内容、附件或 Agent 绑定。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回更新后的会话信息。
""",
)
@inject
async def pin_session(
        session_id: str,
        req: PinSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.set_session_pinned(session_id, user_id, req.set_pin)
    return R.success(data=SessionResponse.from_entity(session))


@router.post(
    "/setSessionAgent",
    response_model=R[SessionResponse],
    status_code=200,
    summary="设置会话 Agent",
    description="""
- 用途：在会话开始前绑定、切换或清除会话使用的 Agent。
- 请求：session_id 指定目标会话；agent_id 为空时清除绑定，非空时指定目标 Agent。
- 约束：当前用户必须已登录；目标会话必须属于当前用户；已有消息的会话不能切换 Agent；agent_id 非空时必须能解析到可用 Agent。
- 处理：校验会话归属和消息状态后写入 Agent ID 与版本，或清空 Agent 绑定；不修改历史消息或附件。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；已有消息的会话切换 Agent -> ChatErrorCode.SESSION_AGENT_CHANGE_FORBIDDEN；Agent 不存在或未发布 -> ChatErrorCode.AGENT_NOT_FOUND；请求参数校验失败 -> ResultCode.PARAM_ERROR。
- 响应：返回更新后的会话信息。
""",
)
@inject
async def set_session_agent(
        session_id: str,
        req: SetSessionAgentRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
        agent_resolver: AgentResolver = Depends(Provide[Container.agent_resolver]),
):
    await session_repo.get_session_for_user(session_id, user_id)
    if await message_repo.has_session_messages(session_id):
        raise ServiceException(ChatErrorCode.SESSION_AGENT_CHANGE_FORBIDDEN)

    if not req.agent_id:
        session = await session_repo.set_session_agent(session_id, user_id, None, None)
        return R.success(data=SessionResponse.from_entity(session))

    agent = await agent_resolver.resolve(req.agent_id)
    if agent is None:
        raise ServiceException(ChatErrorCode.AGENT_NOT_FOUND)

    session = await session_repo.set_session_agent(
        session_id,
        user_id,
        agent.agent_id,
        agent.version
    )
    return R.success(data=SessionResponse.from_entity(session))
