import json
import os
import re
import time
import uuid
from fastapi import APIRouter, Depends, BackgroundTasks
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from common.security import require_login
from chat.api.schemas.chat import ChatRequest
from chat.application.chat_orchestrator import ChatOrchestrator
from chat.application.tools.registry import ToolRegistry
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository

router = APIRouter()


async def _sse_generator(chat_gen, model_name: str):
    """将 orchestrator 的 AsyncGenerator 包装为 OpenAI chat.completion.chunk 兼容的 SSE 格式。"""
    completion_id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())

    async for chunk in chat_gen:
        payload = json.dumps({
            "id": completion_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": model_name,
            "choices": [{
                "index": 0,
                "delta": {"content": chunk},
                "finish_reason": None,
            }],
        }, ensure_ascii=False)
        yield f"data: {payload}\n\n"

    # 最终帧：delta 为空对象，finish_reason 标记为 stop
    final_payload = json.dumps({
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model_name,
        "choices": [{
            "index": 0,
            "delta": {},
            "finish_reason": "stop",
        }],
    }, ensure_ascii=False)
    yield f"data: {final_payload}\n\n"
    yield "data: [DONE]\n\n"


def _truthy(value: str | None) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def _extract_kv(text: str, key: str) -> str | None:
    m = re.search(rf"(?:^|\s){re.escape(key)}\s*=\s*([^\s,;]+)", text)
    if not m:
        return None
    return m.group(1).strip().strip("\"'") or None


@router.post("/completions")
@inject
async def chat_completions(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        user_id: str = Depends(require_login),
        service: ChatOrchestrator = Depends(Provide[Container.chat_service]),
        tool_registry: ToolRegistry = Depends(Provide[Container.tool_registry]),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    """
    POST /v1/chat/completions
    接收用户 Query -> 编排三级记忆 RAG 流程 -> 返回标准 SSE 流
    user_id 从网关透传的 X-User-Id Header 中读取（由 SecurityHeaderMiddleware 解析注入）
    """
    # 鉴权
    await session_repo.get_by_id_and_user(req.session_id, user_id)

    resolved_model = req.model or settings.DEFAULT_MODEL

    if settings.DEV and _truthy(os.getenv("DEVELOPER_ENABLE")) and "run_sandbox_script" in (req.query or ""):
        package_id = _extract_kv(req.query, "package_id")
        entry = _extract_kv(req.query, "entry")

        async def _direct_tool_gen():
            if not package_id:
                yield "[System Error]: missing package_id (use: run_sandbox_script package_id=... entry=...)"
                return
            tool = tool_registry.get("run_sandbox_script")
            text = await tool.execute(
                context={"session_id": req.session_id, "user_id": user_id},
                package_id=package_id,
                entry=entry,
            )
            yield text

        return StreamingResponse(
            _sse_generator(_direct_tool_gen(), resolved_model),  # type: ignore[arg-type]
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
            },
        )

    chat_gen = service.handle_chat(
        user_id=user_id,
        session_id=req.session_id,
        user_query=req.query,
        background_tasks=background_tasks,
        model_name=resolved_model,  # 请求层可覆盖，None 则降级到 settings.DEFAULT_MODEL
    )

    return StreamingResponse(
        _sse_generator(chat_gen, resolved_model),  # type: ignore[arg-type]
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲，确保实时推送
        },
    )
