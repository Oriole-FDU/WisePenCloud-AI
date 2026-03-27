import asyncio
import uuid

from fastapi import APIRouter, Depends, BackgroundTasks, Body, HTTPException
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from chat.api.v1.vercel_formats import message_start, message_finish, stream_abort, stream_end, error

from common.security import require_login
from common.logger import log_event, log_error
from chat.api.schemas.chat import ChatRequest
from chat.application.chat_orchestrator import ChatOrchestrator
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository

router = APIRouter()


async def _vercel_generator(chat_gen, model_name: str):
    """将 orchestrator 的 AsyncGenerator 包装成 vercel ai sdk 格式"""
    try:
        message_id = f"msg_{uuid.uuid4().hex}"
        yield message_start(message_id)

        async for event in chat_gen:
            yield event

        yield message_finish()
        yield stream_end()

    except asyncio.CancelledError:
        yield stream_abort(reason="user_cancelled")
        yield stream_end()
        raise

    except Exception as e:
        log_error("流生成异常", e)
        yield error(error_text=str(e))
        yield stream_end()


@router.post("/completions")
@inject
async def chat_completions(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        user_id: str = Depends(require_login),
        service: ChatOrchestrator = Depends(Provide[Container.chat_service]),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    """
    请求格式:
       {
         "session_id": "xxx",
         "query": "你好",
         "model": "gpt-4"
       }
    """
    resolved_model = req.model or settings.DEFAULT_MODEL
    
    if not req.query:
        raise HTTPException(status_code=400, detail="缺少查询内容")
    
    if not req.session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")

    await session_repo.get_by_id_and_user(req.session_id, user_id)

    chat_gen = service.handle_chat(
        user_id=user_id,
        session_id=req.session_id,
        user_query=req.query,
        background_tasks=background_tasks,
        model_name=resolved_model,
    )

    return StreamingResponse(
        _vercel_generator(chat_gen, resolved_model),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
