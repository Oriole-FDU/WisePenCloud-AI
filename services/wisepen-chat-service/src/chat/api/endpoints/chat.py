import asyncio
import uuid

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from chat.api.vercel_formats import (
    message_start, message_finish, stream_done, abort, error as sse_error,
)

from common.security import require_login
from common.logger import error, info
from chat.api.schemas.chat import ChatRequest
from chat.application.attachment_service import AttachmentService
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository

router = APIRouter()


async def _vercel_generator(chat_gen, model_name: str):
    """将 coordinator 的 AsyncGenerator 包装成 AI SDK 6.x SSE 格式"""
    message_id = f"msg_{uuid.uuid4().hex}"
    try:
        yield message_start(message_id)

        async for event in chat_gen:
            yield event

        yield message_finish()
        yield stream_done()

    except asyncio.CancelledError:
        info("chat stream generation cancelled.")
        yield abort(reason="user_cancelled")
        yield stream_done()
        raise

    except Exception as e:
        error("chat stream generation failed.", exc=e)
        yield sse_error(error_text=str(e))
        yield stream_done()


@router.post("/completions")
@inject
async def chat_completions(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        user_id: str = Depends(require_login),
        coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        attachment_service: AttachmentService = Depends(Provide[Container.attachment_service]),
):
    if not req.query:
        raise HTTPException(status_code=400, detail="缺少查询内容")

    if not req.session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")

    resolved_model_id = PydanticObjectId(req.model or settings.DEFAULT_MODEL_ID)
    resolved_provider_id = PydanticObjectId(req.provider_id) if req.provider_id else None

    session = await session_repo.get_session_for_user(req.session_id, user_id)

    resolved_refs = await attachment_service.resolve_session_attachments(req.session_id)
    effective_refs = req.attachment_refs or resolved_refs

    # 获取完整附件元数据用于注入 LLM 上下文
    session_attachments = await attachment_service.get_session_attachments_meta(req.session_id)
    attachments_meta = None
    if session_attachments:
        attachments_meta = [
            {
                "object_key": a.object_key,
                "original_name": a.original_name,
                "extension": a.extension,
                "file_size": a.file_size,
                "mime_type": a.mime_type,
            }
            for a in session_attachments
        ]

    # 获取未删除的资源引用
    active_resource_refs = [
        r for r in session.resource_refs if not getattr(r, "deleted", False)
    ] if session.resource_refs else None

    chat_gen = coordinator.handle_chat(
        user_id=user_id,
        session_id=req.session_id,
        user_query=req.query,
        background_tasks=background_tasks,
        model_id=resolved_model_id,
        provider_id=resolved_provider_id,
        frontend_states=req.frontend_states,
        attachment_refs=effective_refs,
        attachments_meta=attachments_meta,
        image_b64_list=req.image_b64_list,
        resource_refs=active_resource_refs,
        user_defined_allow_tool_names=req.user_defined_allow_tool_names,
        user_defined_deny_tool_names=req.user_defined_deny_tool_names,
        user_defined_on_demand_skill_ids=req.user_defined_on_demand_skill_ids,
        user_defined_force_enabled_skill_ids=req.user_defined_force_enabled_skill_ids,
    )

    return StreamingResponse(
        _vercel_generator(chat_gen, str(resolved_model_id)),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )

