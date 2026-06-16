from dependency_injector.wiring import inject, Provide
from fastapi import APIRouter, Depends, Query

from chat.api.converters import convert_to_ui_messages
from chat.api.schemas.session import (
    SessionResponse, CreateSessionRequest, RenameSessionRequest,
    PinSessionRequest, SetSessionAgentRequest, UIMessageResponse,
)
from chat.application.agents import AgentResolver
from chat.container import Container
from chat.domain.entities import ChatSession
from chat.domain.error_codes import ChatErrorCode
from chat.domain.repositories import SessionRepository, MessageRepository
from common.core.domain import R, PageResult
from common.core.exceptions import ServiceException
from common.security import require_login

router = APIRouter()

@router.post("/createSession", response_model=R[SessionResponse], status_code=200)
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


@router.get("/listSessions", response_model=R[PageResult[SessionResponse]])
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


@router.post("/deleteSession", response_model=R, status_code=200)
@inject
async def delete_session(
        session_id: str,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.delete_session(session_id, user_id)
    return R.success()


@router.get("/listHistoryMessages", response_model=R[PageResult[UIMessageResponse]])
@inject
async def get_session_messages(
        session_id: str,
        page: int = Query(default=1, ge=1, description="页码，从 1 开始（page=1 为最新回合）"),
        size: int = Query(default=20, ge=1, le=100, description="每页回合数"),
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
):
    await session_repo.get_session_for_user(session_id, user_id)

    page_messages, total_turns = await message_repo.list_session_message_turns_page(session_id, page=page, size=size)
    ui_messages = convert_to_ui_messages(page_messages)

    return R.success(data=PageResult.of(
        items=ui_messages,
        total=total_turns, page=page, size=size,
    ))


@router.post("/renameSession", response_model=R[SessionResponse], status_code=200)
@inject
async def rename_session(
        session_id: str,
        req: RenameSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.rename_session(session_id, user_id, req.new_title or "New Chat")
    return R.success(data=SessionResponse.from_entity(session))

@router.post("/pinSession", response_model=R[SessionResponse], status_code=200)
@inject
async def pin_session(
        session_id: str,
        req: PinSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.set_session_pinned(session_id, user_id, req.set_pin)
    return R.success(data=SessionResponse.from_entity(session))


@router.post("/setSessionAgent", response_model=R[SessionResponse], status_code=200)
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
