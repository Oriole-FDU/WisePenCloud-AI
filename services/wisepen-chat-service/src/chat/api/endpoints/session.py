from fastapi import APIRouter, Depends, Query
from dependency_injector.wiring import inject, Provide

from chat.api.schemas.session import SessionResponse, CreateSessionRequest, MessageResponse, RenameSessionRequest, \
    PinSessionRequest
from chat.domain.entities import ChatSession
from chat.domain.repositories import SessionRepository, MessageRepository
from chat.container import Container

from common.security import require_login
from common.core.domain import R, PageResult

router = APIRouter()

@router.post("/createSession", response_model=R[SessionResponse], status_code=200)
@inject
async def create_session(
        req: CreateSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = ChatSession(user_id=user_id, title=req.title or "New Chat")
    created = await session_repo.create(session)
    return R.success(data=SessionResponse.from_entity(created))


@router.get("/listSessions", response_model=R[PageResult[SessionResponse]])
@inject
async def list_sessions(
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        size: int = Query(default=20, ge=1, le=100, description="每页条数"),
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    sessions, total = await session_repo.get_by_user(user_id, page=page, size=size)
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
    await session_repo.delete(session_id, user_id)
    return R.success()


@router.get("/listHistoryMessages", response_model=R[PageResult[MessageResponse]])
@inject
async def get_session_messages(
        session_id: str,
        page: int = Query(default=1, ge=1, description="页码，从 1 开始"),
        size: int = Query(default=20, ge=1, le=100, description="每页条数"),
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
):
    # 鉴权
    await session_repo.get_by_id_and_user(session_id, user_id)

    messages, total = await message_repo.get_page_by_session(session_id, page=page, size=size)
    return R.success(data=PageResult.of(
        items=[MessageResponse.from_entity(m) for m in messages],
        total=total, page=page, size=size,
    ))


@router.post("/renameSession", response_model=R[SessionResponse], status_code=200)
@inject
async def rename_session(
        session_id: str,
        req: RenameSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.rename(session_id, user_id, req.new_title or "New Chat")
    return R.success(data=SessionResponse.from_entity(session))

@router.post("/pinSession", response_model=R[SessionResponse], status_code=200)
@inject
async def pin_session(
        session_id: str,
        req: PinSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.pin(session_id, user_id, req.set_pin)
    return R.success(data=SessionResponse.from_entity(session))
