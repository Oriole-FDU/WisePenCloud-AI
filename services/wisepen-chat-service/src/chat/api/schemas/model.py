from typing import List
from pydantic import BaseModel, Field

from chat.domain.entities import ModelType


# =============================================================================
# Response Models
# =============================================================================

class ModelInfo(BaseModel):
    """API 响应：模型信息 DTO"""
    id: int
    name: str
    vendor: str
    type: ModelType
    ratio: int
    support_thinking: bool
    support_vision: bool
    is_default: bool


class ModelsResponse(BaseModel):
    """API 响应：模型列表"""
    standard_models: List[ModelInfo] = Field(..., description="标准模型列表")
    advanced_models: List[ModelInfo] = Field(..., description="高级模型列表")
    other_models: List[ModelInfo] = Field(..., description="其他模型列表")
