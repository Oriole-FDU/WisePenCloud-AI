from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field


class SandboxProviderInfo(BaseModel):
    """Provider 创建沙箱所需的镜像和运行地址。"""

    image: str = Field(..., description="容器镜像名称")
    base_url: str | None = None


class SandboxProvider(ABC):
    """沙箱类型适配器接口。"""

    @abstractmethod
    def get_sandbox_provider_info(
        self,
        provider_id: str | None = None,
    ) -> SandboxProviderInfo:
        pass

    @abstractmethod
    async def check_ready(
        self,
        provider_id: str,
        base_url: str | None,
    ) -> bool:
        pass
