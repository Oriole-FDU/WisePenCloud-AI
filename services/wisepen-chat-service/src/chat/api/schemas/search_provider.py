from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field

from chat.application.tools.web.services.web_search.enums import ProviderMode, SearcherName


class SearchProviderConfigResponse(BaseModel):
    """
    API 响应：用户联网搜索通道配置完全体视图
    """
    provider_mode: ProviderMode = Field(..., description="当前生效的搜索模式：DEFAULT / CUSTOM")
    provider: Optional[SearcherName] = Field(default=None, description="CUSTOM 模式下绑定的私有服务商厂牌")
    masked_key: Optional[str] = Field(default=None, description="脱敏后的掩码密钥（如 sk-***xxxx）")
    is_valid: bool = Field(..., description="当前私有通道的连通性/活性健康状态")


class SetSearchProviderModeRequest(BaseModel):
    """
    API 请求：安全切换全局搜索模式
    """
    mode: ProviderMode = Field(..., description="目标搜索模式：DEFAULT / CUSTOM")

    model_config = ConfigDict(extra="forbid")


class SetCustomSearchProviderRequest(BaseModel):
    """
    API 请求：绑定/更新自定义私有搜索源
    """
    provider: SearcherName = Field(..., description="目标搜索服务商厂牌")
    api_key: str = Field(..., description="用户提供的私有明文 API Key")

    model_config = ConfigDict(extra="forbid")