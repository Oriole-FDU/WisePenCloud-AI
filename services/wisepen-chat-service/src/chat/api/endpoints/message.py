from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from chat.api.schemas.message import MessageFeedbackRequest
from chat.domain.entities import ChatMessageFeedback, MessageFeedback
from chat.domain.repositories import MessageFeedbackRepository, MessageRepository, SessionRepository
from chat.container import Container

from common.security import require_login
from common.core.domain import R

router = APIRouter()


@router.post(
    "/feedback",
    response_model=R,
    status_code=200,
    summary="消息点赞/点踩",
    description="""
- 用途：记录或取消当前用户对某条 assistant 回复（UIMessage）的点赞/点踩，仅存储。
- 请求：session_id 指定目标会话；message_id 是 assistant UIMessage 的 id；feedback 为 like/dislike/none（none 取消）。
- 约束：当前用户必须已登录；目标会话必须属于当前用户；feedback 只能为 like/dislike/none。
- 处理：like/dislike 按 (user_id, message_id) 唯一键 upsert；none 删除记录。
- 响应：成功时返回空结果。
""",
)
@inject
async def submit_message_feedback(
        req: MessageFeedbackRequest,
        user_id: str = Depends(require_login),
        session_repo: SessionRepository = Depends(Provide[Container.session_repo]),
        message_repo: MessageRepository = Depends(Provide[Container.message_repo]),
        feedback_repo: MessageFeedbackRepository = Depends(Provide[Container.message_feedback_repo]),
):
    # 校验会话归属及目标消息，防止跨用户、跨会话或非 assistant 消息写入
    await session_repo.get_session_for_user(req.session_id, user_id)
    await message_repo.get_assistant_message_for_session(req.session_id, req.message_id)

    if req.feedback == MessageFeedback.NONE.value:
        await feedback_repo.delete(user_id, req.message_id)
    else:
        await feedback_repo.upsert(ChatMessageFeedback(
            user_id=user_id,
            session_id=req.session_id,
            message_id=req.message_id,
            feedback=MessageFeedback(req.feedback),
        ))
    return R.success()
