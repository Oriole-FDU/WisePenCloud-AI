from typing import Any, Dict
from fastapi import APIRouter
from pydantic import BaseModel


router = APIRouter()


class MemoryItemResponse(BaseModel):
    """单条记忆条目的 API 响应结构"""
    id: str
    memory: str
    metadata: Dict[str, Any] = {}