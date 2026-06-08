from beanie import Document
from pydantic import Field


class Provider(Document):
    """
    供应商配置（存入 MongoDB）
    每个供应商拥有独立的 API 地址和密钥
    """
    name: str = Field(..., description="供应商名称")
    api_base_url: str = Field(..., description="API 网关地址")
    api_key: str = Field(..., description="鉴权密钥")
    usage: int = Field(default=0, description="累计用量（token 数）")
    is_active: bool = Field(default=True, description="是否启用")

    class Settings:
        name = "wisepen_providers"
