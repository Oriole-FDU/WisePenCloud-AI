from beanie import Document, PydanticObjectId
from pydantic import Field


class ModelProviderMapping(Document):
    """
    模型-供应商映射（存入 MongoDB）
    记录每个模型可由哪些供应商提供，以及供应商侧使用的实际模型名
    """
    model_id: int = Field(..., description="关联 Model.id")
    provider_id: PydanticObjectId = Field(..., description="关联 Provider._id")
    provider_model_name: str = Field(..., description="供应商侧实际模型名（如 openai/gpt-4o）")
    is_preferred: bool = Field(default=False, description="是否为首选供应商")

    class Settings:
        name = "wisepen_model_provider_mappings"
