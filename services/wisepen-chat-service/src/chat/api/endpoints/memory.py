from typing import List, Any, Dict
from fastapi import APIRouter, Depends, Query
from dependency_injector.wiring import inject, Provide

from chat.api.schemas.memory import MemoryItemResponse
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import MemoryProvider
from chat.container import Container

from common.security import require_login
from common.core.exceptions import ServiceException
from common.core.domain import R

router = APIRouter()


@router.get(
    "/listMemories",
    response_model=R[List[MemoryItemResponse]],
    summary="查询长期记忆",
    description="""
- 用途：查询当前用户的长期记忆条目，用于记忆管理面板展示。
- 请求：limit 为返回上限，默认 100，范围 1～1000；用户身份来自请求上下文。
- 约束：当前用户必须已登录；长期记忆 provider 必须可用。
- 处理：调用 MemoryProvider 读取当前用户记忆，并转换为记忆 ID、内容和 metadata；不修改记忆内容。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；读取失败 -> ChatErrorCode.MEMORY_OPERATION_FAILED；格式错误 -> ChatErrorCode.MEMORY_RESPONSE_INVALID。
- 响应：返回最多 limit 条记忆，不包含总数或分页游标；达到上限时可能仍有未返回的条目。
""",
)
@inject
async def list_memories(
    limit: int = Query(default=100, ge=1, le=1000),
    user_id: str = Depends(require_login),
    memory: MemoryProvider = Depends(Provide[Container.memory_provider]),
):
    try:
        items = await memory.get_all(user_id=user_id, limit=limit)
    except ServiceException:
        raise
    except Exception:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED) from None
    return R.success(data=[
        MemoryItemResponse(
            id=str(item.get("id", "")),
            memory=item.get("memory", ""),
            metadata=item.get("metadata") or {},
        )
        for item in items
    ])


@router.post(
    "/deleteMemory",
    response_model=R,
    status_code=200,
    summary="删除长期记忆",
    description="""
- 用途：删除当前用户的一条长期记忆，用于用户主动纠错或清理记忆。
- 请求：memory_id 指定目标记忆。
- 约束：当前用户必须已登录；目标记忆删除操作必须被 MemoryProvider 接受。
- 处理：调用 MemoryProvider 删除指定记忆；不返回删除后的列表。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；记忆 provider 拒绝或删除失败 -> ChatErrorCode.MEMORY_OPERATION_FAILED。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_memory(
    memory_id: str,
    user_id: str = Depends(require_login),
    memory: MemoryProvider = Depends(Provide[Container.memory_provider]),
):
    try:
        await memory.delete_memory(memory_id=memory_id, user_id=user_id)
    except PermissionError:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED)
    except Exception as e:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED, custom_msg=str(e))
    return R.success()


@router.delete(
    "/deleteAllMemories",
    response_model=R,
    status_code=200,
    summary="清空长期记忆",
    description="""
- 用途：清空当前用户的全部长期记忆，用于隐私清理或重置个人记忆。
- 请求：无业务请求参数，用户身份来自请求上下文。
- 约束：当前用户必须已登录；长期记忆 provider 必须可用。
- 处理：调用 MemoryProvider 删除当前用户全部记忆；不删除会话、消息或附件。
- 失败：未登录 -> PermissionErrorCode.NOT_LOGIN；记忆 provider 调用失败 -> ChatErrorCode.MEMORY_OPERATION_FAILED。
- 响应：成功时返回空结果。
""",
)
@inject
async def delete_all_memories(
    user_id: str = Depends(require_login),
    memory: MemoryProvider = Depends(Provide[Container.memory_provider]),
):
    try:
        await memory.delete_all_for_user(user_id=user_id)
    except Exception as e:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED, custom_msg=str(e))
    return R.success()
