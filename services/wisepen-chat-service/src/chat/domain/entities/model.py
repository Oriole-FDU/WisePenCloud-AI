from enum import IntEnum
from beanie import Document
from pydantic import Field


class ModelType(IntEnum):
    STANDARD_MODEL = 1
    ADVANCED_MODEL = 2
    UNKNOWN_MODEL = 3


class Model(Document):
    """
    模型配置（存入 MongoDB）
    仅包含前端可见的模型元信息，不含供应商细节
    """
    id: int = Field(..., description="模型序号ID")
    display_name: str = Field(..., description="展示名称（如 GPT-4o）")
    vendor: str = Field(..., description="模型厂商（如 OpenAI、Google、DeepSeek）")
    type: ModelType = Field(..., description="模型类型")
    billing_ratio: int = Field(default=1, description="计费倍率")
    support_thinking: bool = Field(default=False, description="是否支持深度思考")
    support_vision: bool = Field(default=False, description="是否具备视觉能力")
    is_active: bool = Field(default=True, description="是否启用")

    class Settings:
        name = "wisepen_models"
