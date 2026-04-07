from typing import List
from pydantic import BaseModel, Field

from chat.domain.entities import ModelType, ProviderMap


# =============================================================================
# Response Models
# =============================================================================

class ModelInfo(BaseModel):
    """API 响应：模型信息 DTO"""
    id: str
    name: str
    type: ModelType
    providers: List[ProviderMap]
    ratio: int
    is_default: bool


class ModelsResponse(BaseModel):
    """API 响应：模型列表"""
    standard_models: List[ModelInfo] = Field(..., description="标准模型列表")
    advanced_models: List[ModelInfo] = Field(..., description="高级模型列表")
    other_models: List[ModelInfo] = Field(..., description="其他模型列表")
