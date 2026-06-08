import asyncio
import uuid

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import StreamingResponse

from chat.api.schemas.chat import ChatRequest
from chat.api.vercel_formats import (
    abort,
    error,
    message_finish,
    message_start,
    stream_done,
)
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository
from common.logger import log_error, log_event
from common.security import require_login

router = APIRouter()


async def _vercel_generator(chat_gen):
    """将 coordinator 的 AsyncGenerator 包装成 AI SDK 6.x SSE 格式"""
    message_id = f"msg_{uuid.uuid4().hex}"
    try:
        yield message_start(message_id)

        async for event in chat_gen:
            yield event

        yield message_finish()
        yield stream_done()

    except asyncio.CancelledError:
        log_event("用户取消请求")
        yield message_finish()
        yield abort(reason="user_cancelled")
        yield stream_done()
        raise

    except Exception as e:
        log_error("流生成", e)
        yield message_finish()
        yield error(error_text=str(e))
        yield stream_done()


@router.post("/completions")
@inject
async def chat_completions(
    req: ChatRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(require_login),
    coordinator: ChatTurnCoordinator = Depends(
        Provide[Container.chat_turn_coordinator]
    ),
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
    resolved_model_id = req.model if req.model is not None else settings.DEFAULT_MODEL_ID

    await session_repo.get_by_id_and_user(req.session_id, user_id)

    chat_gen = coordinator.handle_chat(
        user_id=user_id,
        session_id=req.session_id,
        user_query=req.query,
        background_tasks=background_tasks,
        model_id=resolved_model_id,
        states=req.states,
    )

    return StreamingResponse(
        _vercel_generator(chat_gen),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )
