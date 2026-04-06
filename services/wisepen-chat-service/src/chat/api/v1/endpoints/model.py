from fastapi import APIRouter
from typing import List
from enum import IntEnum
import re
from pydantic import BaseModel, Field, computed_field

from chat.core.config.app_settings import settings
from common.core.domain import R

router = APIRouter()


class ModelType(IntEnum):
    STANDARD_MODEL = 1
    ADVANCED_MODEL = 2
    UNKNOWN_MODEL = 3

    @property
    def ratio(self) -> int:
        return {1: 1, 2: 10, 3: 1}[self.value]


def get_model_name(model_id: str) -> str:
    """
    获取模型名称, 后续可能拓展其他模型的名称格式
    e.g. gpt-4o-mini -> GPT-4o Mini
    claude-3.5-sonnet-20241012 -> Claude 3.5 Sonnet
    gemini-3.5-pro -> Gemini 3.5 Pro
    """
    # 去除日期，如 2024-10-12, 20241012, 2024-1012
    clean_id = re.sub(r'-\d{8}$|-\d{4}-\d{2}-\d{2}$|-\d{4}-\d{4}$', '', model_id)
    base_name = ' '.join(word.capitalize() for word in clean_id.split('-'))

    if model_id.startswith('gpt'):
        return base_name.replace('Gpt', 'GPT').replace(' ', '-', 1)

    return base_name


def get_model_type(model_id: str) -> ModelType:
    """
    获取模型类型
    """
    if model_id in settings.STANDARD_MODELS:
        return ModelType.STANDARD_MODEL
    elif model_id in settings.ADVANCED_MODELS:
        return ModelType.ADVANCED_MODEL
    else:
        return ModelType.UNKNOWN_MODEL


class ModelInfo(BaseModel):
    id: str = Field(..., description="模型ID")
    name: str = Field(default="", description="模型名称")
    type: ModelType = Field(default=ModelType.UNKNOWN_MODEL, description="模型类型")

    @property
    def ratio(self) -> int:
        return self.type.ratio

    @computed_field
    @property
    def is_default(self) -> bool:
        return self.id == settings.DEFAULT_MODEL

    def model_post_init(self, __context):
        """自动生成name, type"""
        if not self.name:
            self.name = get_model_name(self.id)
        if self.type == ModelType.UNKNOWN_MODEL:
            self.type = get_model_type(self.id)


class ModelsResponse(BaseModel):
    models: List[ModelInfo] = Field(..., description="模型列表")


@router.get("/list", response_model=R[ModelsResponse])
async def get_models():
    models = [ModelInfo(id=model_id) for model_id in settings.MODEL_LIST]
    return R.success(data=ModelsResponse(models=models))