from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, Field

from sandbox_v1.domain.entities import SandboxEndpointRef

class SandboxProviderInfo(BaseModel):
    start_spec: SandboxSpecInfo
    endpoint: SandboxEndpointRef

class SandboxSpecInfo(BaseModel):
    """Provider 创建沙箱所需的最小规格。"""

    image: str = Field(..., description="容器镜像名称")
    cpu_cores: float | None = Field(default=None, description="申请的 CPU 核心数")
    memory_mb: int | None = Field(default=None, description="申请的内存大小，单位 MB")
    environment: dict[str, str] = Field(default_factory=dict, description="启动容器时注入的环境变量")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class SandboxRegistry:
    """沙箱运行信息注册表的空实现。"""

    def get_sandbox_provider_info(self, provider_id: str | None = None) -> SandboxProviderInfo:...

    async def check_ready(self, provider_id: str, endpoint: SandboxEndpointRef | None) -> bool:...
