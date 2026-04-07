from fastapi import APIRouter, Depends, Query
from dependency_injector.wiring import inject, Provide
from pydantic import BaseModel, Field
from typing import List, Optional, Any, Dict
from datetime import datetime

from common.security import require_login
from common.core.domain import R, PageResult
from chat.container import Container
from chat.domain.entities import ChatSession, ChatMessage
from chat.domain.repositories import SessionRepository, MessageRepository

router = APIRouter()


# ---------------------------------------------------------------------------
# Request / Response 模型
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    title: Optional[str] = Field(default="New Chat", description="会话标题")

class RenameSessionRequest(BaseModel):
    new_title: Optional[str] = Field(default=None, description="新会话标题")

class PinSessionRequest(BaseModel):
    set_pin: bool = Field(default=False, description="是否置顶")


class SessionResponse(BaseModel):
    id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    is_pinned: bool = False
    pinned_at: Optional[datetime] = None

    @classmethod
    def from_entity(cls, session: ChatSession) -> "SessionResponse":
        return cls(
            id=str(session.id) if session.id else "",
            user_id=session.user_id,
            title=session.title,
            created_at=session.created_at.isoformat(),
            updated_at=session.updated_at.isoformat(),
            is_pinned=session.is_pinned,
            pinned_at=session.pinned_at.isoformat() if session.pinned_at else None,
        )


class MessageResponse(BaseModel):
    """
    会话消息条目响应。
    - user / assistant 消息：完整返回 content 和 tool_calls。
    - TOOL role（工具调用结果）：在仓储层已过滤，不会出现在此处。
    """
    id: str
    role: str
    content: Optional[str]
    tool_calls: Optional[List[Dict[str, Any]]]
    created_at: str

    @classmethod
    def from_entity(cls, msg: ChatMessage) -> "MessageResponse":
        return cls(
            id=str(msg.id) if msg.id else "",
            role=msg.role.value,
            content=msg.content,
            tool_calls=msg.tool_calls,
            created_at=msg.created_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@router.post("/create", response_model=R[SessionResponse], status_code=200)
@inject
async def create_session(
        req: CreateSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = ChatSession(user_id=user_id, title=req.title or "New Chat")
    created = await session_repo.create(session)
    return R.success(data=SessionResponse.from_entity(created))


@router.get("/list", response_model=R[PageResult[SessionResponse]])
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


@router.delete("/{session_id}", response_model=R, status_code=200)
@inject
async def delete_session(
        session_id: str,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    await session_repo.delete(session_id, user_id)
    return R.success()


@router.get("/{session_id}/messages", response_model=R[PageResult[MessageResponse]])
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


@router.post("/{session_id}/rename", response_model=R[SessionResponse], status_code=200)
@inject
async def rename_session(
        session_id: str,
        req: RenameSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.rename(session_id, user_id, req.new_title or "New Chat")
    return R.success(data=SessionResponse.from_entity(session))

@router.post("/{session_id}/pin", response_model=R[SessionResponse], status_code=200)
@inject
async def pin_session(
        session_id: str,
        req: PinSessionRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    session = await session_repo.set_pin(session_id, user_id, req.set_pin)
    return R.success(data=SessionResponse.from_entity(session))
