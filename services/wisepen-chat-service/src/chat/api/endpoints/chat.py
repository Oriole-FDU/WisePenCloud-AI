import asyncio
import uuid

from beanie import PydanticObjectId
from fastapi import APIRouter, Depends, BackgroundTasks, HTTPException
from fastapi.responses import StreamingResponse
from dependency_injector.wiring import inject, Provide

from chat.api.vercel_formats import (
    message_start, message_finish, stream_done, error as sse_error,
)

from common.security import require_login
from common.security.context import _security_context
from common.gray.context import _gray_context
from common.logger import error, info
from chat.api.schemas.chat import ChatRequest
from chat.application.chat_turn_coordinator import ChatTurnCoordinator
from chat.container import Container
from chat.core.config.app_settings import settings
from chat.domain.repositories import SessionRepository

router = APIRouter()


async def _vercel_generator(
        chat_gen,
        model_name: str,
        *,
        security_context: dict | None = None,
        gray_context: str = "",
):
    """将 coordinator 的 AsyncGenerator 包装成 AI SDK 6.x SSE 格式"""
    message_id = f"msg_{uuid.uuid4().hex}"
    _security_context.set(dict(security_context or {}))
    _gray_context.set(gray_context or "")
    try:
        yield message_start(message_id)

        async for event in chat_gen:
            yield event

        yield message_finish()
        yield stream_done()

    except asyncio.CancelledError:
        info("chat stream generation cancelled.")
        raise

    except Exception as e:
        error("chat stream generation failed.", exc=e)
        yield sse_error(error_text=str(e))
        yield stream_done()
    finally:
        _security_context.set({})
        _gray_context.set("")


@router.post(
    "/completions",
    summary="发送流式对话",
    description="""
- 用途：在指定会话中发起一轮 Chat Turn，用于把用户最新输入交给当前会话绑定的 Agent 编排执行，并以流式事件返回模型推理、工具调用和最终回复。
- 请求：session_id 指定目标会话；query 是本轮用户输入；model 可选指定模型 ID，未传时使用 DEFAULT_MODEL_ID，若会话 Agent 的 model_policy 不允许请求覆盖则改用 Agent 默认模型；provider_id 可选指定该模型的一条 active Provider 映射，未传时选择首选映射；runtime_options 覆盖 Provider manifest 默认运行参数；frontend_states 会筛选未禁用且有值的前端状态写入应用上下文；user_defined_attachment_ids 仅用于标记本轮重点附件，实际可见附件仍来自会话已关联的临时附件和资源附件；allow/deny tool 与 on-demand skill 参数用于覆盖 Agent 的本轮工具和 Skill 可见性策略。
- 约束：当前用户必须已登录；query 和 session_id 不能为空；目标会话必须属于当前用户；model、provider_id 必须是合法 ObjectId；目标模型必须是 active 的用户模型或系统模型；provider_id 必须属于该模型的 active 映射；Provider 必须 active；runtime_options 必须符合目标 Provider 的 JSON Schema；工具、Skill、记忆和模型覆盖最终受会话 Agent 策略约束。
- 处理：先校验会话归属，再读取会话绑定 Agent，没有绑定时使用默认 Agent；根据 Agent model_policy 解析最终模型、Provider、Provider 侧模型名和运行参数；按 Agent memory_policy 加载 Redis 热上下文，必要时从 MongoDB 回填，按配置召回长期记忆和会话摘要；按工具与 Skill 策略匹配本轮可展示 Skill、派生 ToolScope，并读取会话临时附件和资源附件；组装 system prompt、历史摘要、历史明细、长期记忆、前端状态、Skill metadata、附件清单和用户 query 后进入多步 ReAct 循环；循环中把 Provider 原生流转换为 AI SDK 6.x UIMessage Stream 事件，工具调用会先输出输入事件、并发执行工具，再输出工具结果并继续下一步模型推理；响应返回后通过 BackgroundTasks 发送 token 计费、追加 Redis 热上下文、按配置落 MongoDB、写入长期记忆、压缩摘要并在需要时自动生成标题。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；query 或 session_id 为空 -> HTTP 400；会话不存在或不属于当前用户 -> ChatErrorCode.SESSION_NOT_FOUND；模型不存在、未启用或不可访问 -> ChatErrorCode.MODEL_NOT_FOUND；模型供应商映射不存在或未启用 -> ChatErrorCode.MODEL_MAPPING_NOT_FOUND；Provider 不存在或未启用 -> ChatErrorCode.PROVIDER_NOT_FOUND；Provider 类型无对应运行时适配器 -> ChatErrorCode.MODEL_PROVIDER_TYPE_UNSUPPORTED；runtime_options 不符合目标 Provider schema -> ChatErrorCode.MODEL_RUNTIME_OPTIONS_INVALID；上下文超过模型限制 -> ChatErrorCode.CONTEXT_LIMIT_EXCEEDED；大模型或 Provider 流式调用失败 -> ChatErrorCode.LLM_GENERATION_FAILED。
- 响应：返回 text/event-stream，并设置 x-vercel-ai-ui-message-stream=v1；事件使用 AI SDK 6.x UIMessage Stream 语义，外层先发送 {"type":"start","messageId":...}，每个 ReAct step 可能包含 start-step、reasoning-start/reasoning-delta/reasoning-end、text-start/text-delta/text-end、tool-input-start、tool-input-available、tool-output-available、finish-step，最后发送 {"type":"finish"} 和 data: [DONE]；流中业务异常会以 {"type":"error","errorText":...} 事件返回，客户端断开时停止生成并释放流上下文。
""",
)
@inject
async def chat_completions(
        req: ChatRequest,
        background_tasks: BackgroundTasks,
        user_id: str = Depends(require_login),
        coordinator: ChatTurnCoordinator = Depends(Provide[Container.chat_turn_coordinator]),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
):
    if not req.query:
        raise HTTPException(status_code=400, detail="缺少查询内容")

    if not req.session_id:
        raise HTTPException(status_code=400, detail="缺少 session_id")

    resolved_model_id = PydanticObjectId(req.model or settings.DEFAULT_MODEL_ID)
    resolved_provider_id = PydanticObjectId(req.provider_id) if req.provider_id else None

    await session_repo.get_session_for_user(req.session_id, user_id)
    stream_security_context = _security_context.get().copy()
    stream_gray_context = _gray_context.get()

    chat_gen = coordinator.handle_chat(
        user_id=user_id,
        session_id=req.session_id,
        user_query=req.query,
        background_tasks=background_tasks,
        model_id=resolved_model_id,
        provider_id=resolved_provider_id,
        runtime_options=req.runtime_options,
        frontend_states=req.frontend_states,
        user_defined_attachment_ids=req.user_defined_attachment_ids,
        user_defined_allow_tool_names=req.user_defined_allow_tool_names,
        user_defined_deny_tool_names=req.user_defined_deny_tool_names,
        user_defined_on_demand_skill_ids=req.user_defined_on_demand_skill_ids,
        user_defined_force_enabled_skill_ids=req.user_defined_force_enabled_skill_ids,
    )

    return StreamingResponse(
        _vercel_generator(
            chat_gen,
            str(resolved_model_id),
            security_context=stream_security_context,
            gray_context=stream_gray_context,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "x-vercel-ai-ui-message-stream": "v1",
        },
    )

