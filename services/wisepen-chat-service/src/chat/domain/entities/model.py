from typing import List
from enum import IntEnum
from beanie import Document
from pydantic import Field, BaseModel

from chat.core.config.app_settings import settings


# =============================================================================
# Enums
# =============================================================================

class ProviderId(IntEnum):
    ZHIZENGZENG = 1
    APIYI = 2
    MODELSCOPE = 3


class ModelType(IntEnum):
    STANDARD_MODEL = 1
    ADVANCED_MODEL = 2
    UNKNOWN_MODEL = 3

    @property
    def ratio(self) -> int:
        return {1: 1, 2: 10, 3: 1}[self.value]


# =============================================================================
# BaseModel
# =============================================================================

class ProviderMap(BaseModel):
    """
    嵌入文档：供应商映射
    记录统一模型ID与供应商实际模型ID的对应关系
    """
    provider_id: ProviderId = Field(..., description="供应商ID")
    model_id: str = Field(..., description="供应商实际模型ID")


# =============================================================================
# Helper Functions
# =============================================================================

def get_model_name(model_id: str) -> str:
    """
    格式化模型名称
    e.g. gpt-4o-mini -> GPT-4o Mini
    """
    base_name = ' '.join(word.capitalize() for word in model_id.split('-'))
    if model_id.startswith('gpt'):
        return base_name.replace('Gpt', 'GPT').replace(' ', '-', 1)
    if model_id.startswith('deepseek'):
        return base_name.replace('Deepseek', 'DeepSeek')
    if model_id.startswith('glm'):
        return base_name.replace('Glm', 'GLM')
    return base_name


def get_model_type(model_id: str) -> ModelType:
    """
    根据配置判断模型类型
    """
    if model_id in settings.STANDARD_MODELS:
        return ModelType.STANDARD_MODEL
    elif model_id in settings.ADVANCED_MODELS:
        return ModelType.ADVANCED_MODEL
    return ModelType.UNKNOWN_MODEL


# =============================================================================
# Documents
# =============================================================================

class ModelConfig(Document):
    """
    模型配置（存入 MongoDB）
    """
    id: str = Field(..., description="统一模型ID")
    providers: List[ProviderMap] = Field(default_factory=list, description="供应商映射列表")
    is_active: bool = Field(default=True, description="是否启用")

    @property
    def name(self) -> str:
        return get_model_name(self.id)
    
    @property
    def type(self) -> ModelType:
        return get_model_type(self.id)
    
    @property
    def ratio(self) -> int:
        return self.type.ratio
    
    class Settings:
        name = "wisepen_model_configs"
