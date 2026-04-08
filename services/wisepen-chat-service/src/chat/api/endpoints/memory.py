from typing import List, Any, Dict
from fastapi import APIRouter, Depends
from dependency_injector.wiring import inject, Provide

from chat.api.schemas.memory import MemoryItemResponse
from chat.domain.error_codes import ChatErrorCode
from chat.domain.interfaces import MemoryProvider
from chat.container import Container

from common.security import require_login
from common.core.exceptions import ServiceException
from common.core.domain import R

router = APIRouter()


@router.get("/listMemories", response_model=R[List[MemoryItemResponse]])
@inject
async def list_memories(
    user_id: str = Depends(require_login),
    memory: MemoryProvider = Depends(Provide[Container.memory_provider]),
):
    """GET /chat/memories — 返回当前用户的全部长期记忆条目，供记忆管理面板展示。"""
    try:
        items = await memory.get_all(user_id=user_id)
    except Exception as e:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED, custom_msg=str(e))
    return R.success(data=[
        MemoryItemResponse(
            id=str(item.get("id", "")),
            memory=item.get("memory", ""),
            metadata=item.get("metadata") or {},
        )
        for item in items
    ])


@router.post("/deleteMemory", response_model=R, status_code=200)
@inject
async def delete_memory(
    memory_id: str,
    user_id: str = Depends(require_login),
    memory: MemoryProvider = Depends(Provide[Container.memory_provider]),
):
    """DELETE /chat/memories/{memory_id} — 删除单条长期记忆，用于用户主动纠错（幻觉修正）场景。"""
    try:
        await memory.delete_memory(memory_id=memory_id, user_id=user_id)
    except PermissionError:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED, custom_msg="无权删除该记忆条目")
    except Exception as e:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED, custom_msg=str(e))
    return R.success()


@router.delete("/deleteAllMemories", response_model=R, status_code=200)
@inject
async def delete_all_memories(
    user_id: str = Depends(require_login),
    memory: MemoryProvider = Depends(Provide[Container.memory_provider]),
):
    """DELETE /chat/memories — 清空当前用户的全部长期记忆，满足 GDPR 等隐私合规注销需求。"""
    try:
        await memory.delete_all_for_user(user_id=user_id)
    except Exception as e:
        raise ServiceException(ChatErrorCode.MEMORY_OPERATION_FAILED, custom_msg=str(e))
    return R.success()
