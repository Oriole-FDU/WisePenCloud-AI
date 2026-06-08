from datetime import datetime, timezone

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query

from chat.api.converters import convert_to_ui_messages
from chat.api.schemas.session import (
    CreateSessionRequest,
    PinSessionRequest,
    RenameSessionRequest,
    RollbackSessionRequest,
    SessionResponse,
    UIMessageResponse,
)
from chat.container import Container
from chat.domain.entities import ChatSession
from chat.domain.repositories import (
    HotContextRepository,
    MessageRepository,
    SessionRepository,
)
from common.core.domain import PageResult, R
from common.security import require_login

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
    return R.success(
        data=PageResult.of(
            items=[SessionResponse.from_entity(s) for s in sessions],
            total=total,
            page=page,
            size=size,
        )
    )


@router.post("/deleteSession", response_model=R, status_code=200)
@inject
async def delete_session(
    session_id: str,
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.delete(session_id, user_id)
    return R.success()


@router.post("/rollbackToMessage", response_model=R, status_code=200)
@inject
async def rollback_to_message(
    session_id: str,
    req: RollbackSessionRequest,
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
    message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
    hot_context_repo: HotContextRepository = Depends(
        Provide[Container.hot_context_repo]
    ),
):
    session = await session_repo.get_by_id_and_user(session_id, user_id)
    deleted_count = await message_repo.delete_from_message(session_id, req.message_id)
    if deleted_count == 0:
        raise HTTPException(status_code=404, detail="message not found")

    await hot_context_repo.clear(session_id)
    session.current_summary = None
    session.summary_updated_at = None
    session.updated_at = datetime.now(timezone.utc)
    await session.save()
    return R.success(data={"deleted_count": deleted_count})


@router.get("/listHistoryMessages", response_model=R[PageResult[UIMessageResponse]])
@inject
async def get_session_messages(
    session_id: str,
    page: int = Query(
        default=1, ge=1, description="页码，从 1 开始（page=1 为最新回合）"
    ),
    size: int = Query(default=20, ge=1, le=100, description="每页回合数"),
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
    message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
):
    await session_repo.get_by_id_and_user(session_id, user_id)

    page_messages, total_turns = await message_repo.get_page_for_ui(
        session_id, page=page, size=size
    )
    ui_messages = convert_to_ui_messages(page_messages)

    return R.success(
        data=PageResult.of(
            items=ui_messages,
            total=total_turns,
            page=page,
            size=size,
        )
    )


@router.post("/renameSession", response_model=R[SessionResponse], status_code=200)
@inject
async def rename_session(
    session_id: str,
    req: RenameSessionRequest,
    user_id: str = Depends(require_login),
    session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.rename(
        session_id, user_id, req.new_title or "New Chat"
    )
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
