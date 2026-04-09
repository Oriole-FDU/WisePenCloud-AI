import asyncio
import uuid

from fastapi import APIRouter, Depends, BackgroundTasks, Body, HTTPException
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from chat.api.vercel_formats import message_finish, stream_abort, error

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
        async for event in chat_gen:
            yield event

        yield message_finish()

    except asyncio.CancelledError:
        log_event("用户取消请求")
        yield stream_abort(reason="user_cancelled")
        raise

    except Exception as e:
        log_error("流生成", e)
        yield error(error_text=str(e))


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
         "model": 1,
         "states": [{
            "key": "selected_text",
            "value": "xxx",
            "disabled": false}
         ]
       }
    """
    resolved_model_id = req.model or settings.DEFAULT_MODEL
    
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
        model_id=resolved_model_id,
        states=req.states
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
