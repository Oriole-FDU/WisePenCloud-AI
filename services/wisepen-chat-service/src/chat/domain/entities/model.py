from datetime import datetime, timezone
from enum import Enum, IntEnum
from typing import Optional

from beanie import Document, PydanticObjectId
from pydantic import Field
from pymongo import ASCENDING, DESCENDING, IndexModel

class ModelScope(str, Enum):
    SYSTEM = "SYSTEM"  # 平台内置模型
    USER = "USER"      # 用户自定义模型

class ModelType(IntEnum):
    CUSTOM_MODEL = 0
    STANDARD_MODEL = 1
    ADVANCED_MODEL = 2
    UNKNOWN_MODEL = 3


class Model(Document):
    """
    模型配置，前端可见的模型元信息
    """
    display_name: str = Field(..., description="展示名称（如 GPT-4o）")
    vendor: str = Field(..., description="模型厂商（如 OpenAI、Google、DeepSeek）")

    scope: ModelScope = Field(default=ModelScope.SYSTEM, description="模型作用域")
    owner_user_id: Optional[str] = Field(default=None, description="USER 作用域下的归属用户 ID")

    type: ModelType = Field(default=ModelType.CUSTOM_MODEL, description="模型展示分组类型")

    billing_ratio: int = Field(default=1, description="计费倍率")

    support_thinking: bool = Field(default=False, description="是否支持深度思考")
    support_vision: bool = Field(default=False, description="是否支持视觉输入")
    support_tools: bool = Field(default=True, description="是否支持 tool calling")
    support_streaming: bool = Field(default=True, description="是否支持流式输出")

    context_window_tokens: Optional[int] = Field(default=None, description="上下文窗口 token 上限")
    max_output_tokens: Optional[int] = Field(default=None, description="最大输出 token 数")

    is_active: bool = Field(default=True, description="是否启用")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_models"
        indexes = [
            IndexModel(
                [("scope", ASCENDING), ("owner_user_id", ASCENDING), ("is_active", ASCENDING),
                 ("updated_at", DESCENDING)],
                name="idx_model_scope_owner_active_updated",
            ),
            IndexModel(
                [("scope", ASCENDING), ("owner_user_id", ASCENDING), ("type", ASCENDING), ("is_active", ASCENDING)],
                name="idx_model_scope_owner_type_active",
            ),
            IndexModel(
                [("scope", ASCENDING), ("owner_user_id", ASCENDING), ("display_name", ASCENDING)],
                unique=True,
                name="uniq_model_scope_owner_display_name",
            ),
        ]

class ModelProviderMapping(Document):
    """
    模型-供应商映射
    记录每个模型可由哪些供应商提供，以及供应商侧使用的实际模型名
    """
    model_id: PydanticObjectId = Field(..., description="关联 Model._id")
    provider_id: PydanticObjectId = Field(..., description="关联 Provider._id")
    provider_model_name: str = Field(..., description="供应商侧实际模型名（如 openai/gpt-4o）")

    owner_user_id: Optional[str] = Field(default=None, description="归属用户 ID")

    is_preferred: bool = Field(default=False, description="是否为首选供应商")
    is_active: bool = Field(default=True, description="是否启用")
    priority: int = Field(default=0, description="同一模型多个供应商时的优先级")

    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    class Settings:
        name = "wisepen_model_provider_mappings"
        indexes = [
            IndexModel(
                [("model_id", ASCENDING), ("owner_user_id", ASCENDING), ("is_active", ASCENDING),
                 ("priority", ASCENDING), ("created_at", ASCENDING)],
                name="idx_mapping_model_owner_active_priority_created",
            ),
            IndexModel(
                [("provider_id", ASCENDING), ("owner_user_id", ASCENDING), ("is_active", ASCENDING)],
                name="idx_mapping_provider_owner_active",
            ),
            IndexModel(
                [("model_id", ASCENDING), ("provider_id", ASCENDING), ("owner_user_id", ASCENDING)],
                unique=True,
                name="uniq_mapping_model_provider_owner",
            ),
            IndexModel(
                [("model_id", ASCENDING), ("owner_user_id", ASCENDING), ("is_preferred", ASCENDING)],
                unique=True,
                partialFilterExpression={"is_preferred": True, "is_active": True},
                name="uniq_mapping_one_active_preferred_per_model_owner",
            ),
        ]

