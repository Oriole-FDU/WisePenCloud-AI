from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

from sandbox_v1.domain.entities import SandboxDocument, SandboxEndpointRef, SandboxState
from sandbox_v1.domain.repositories import SandboxRepository

if TYPE_CHECKING:
    from sandbox_v1.domain.interfaces.container_manager import ContainerManager


class SandboxSpecInfo(BaseModel):
    """Provider 创建沙箱所需的最小规格。"""

    image: str = Field(..., description="容器镜像名称")
    cpu_cores: float | None = Field(default=None, description="申请的 CPU 核心数")
    memory_mb: int | None = Field(default=None, description="申请的内存大小，单位 MB")
    environment: dict[str, str] = Field(default_factory=dict, description="启动容器时注入的环境变量")
    metadata: dict[str, Any] = Field(default_factory=dict, description="附加元数据")


class SandboxProviderInfo(BaseModel):
    start_spec: SandboxSpecInfo
    container_ip: str | None = None
    endpoint: SandboxEndpointRef | None = None


class SandboxRegistry:
    """沙箱运行信息注册表，负责把容器登记为 Sandbox。"""

    def __init__(
        self,
        sandbox_repository: SandboxRepository,
        *,
        container_manager: ContainerManager | None = None,
        provider_id: str | None = None,
        endpoint: SandboxEndpointRef | None = None,
    ) -> None:
        if container_manager is None:
            from sandbox_v1.domain.interfaces.container_manager import ContainerManager

            container_manager = ContainerManager()
        self._sandbox_repository = sandbox_repository
        self._container_manager = container_manager
        self._provider_id = provider_id
        self._endpoint = endpoint

    def get_sandbox_provider_info(self, provider_id: str | None = None) -> SandboxProviderInfo:
        from sandbox_v1.core.config.app_settings import settings

        return SandboxProviderInfo(
            start_spec=SandboxSpecInfo(image=settings.SANDBOX_IMAGE),
            endpoint=self._endpoint,
        )

    async def register_container(
        self,
        container_id: str,
        sandbox_provider_info: SandboxProviderInfo,
        *,
        provider_id: str | None = None,
    ) -> SandboxDocument:
        resolved_provider_id = provider_id or self._provider_id
        if resolved_provider_id is None:
            from sandbox_v1.core.config.app_settings import settings

            resolved_provider_id = settings.SANDBOX_PROVIDER_ID

        if not sandbox_provider_info.container_ip:
            raise ValueError("sandbox provider info missing container_ip")

        now = datetime.now(timezone.utc)
        sandbox = SandboxDocument(
            container_id=container_id,
            container_ip=sandbox_provider_info.container_ip,
            provider_id=resolved_provider_id,
            endpoint=sandbox_provider_info.endpoint if sandbox_provider_info.endpoint is not None else self._endpoint,
            state=SandboxState.WARMING,
            updated_at=now,
        )
        await self._sandbox_repository.save(sandbox)
        return sandbox

    async def check_ready(self, sandbox: SandboxDocument) -> bool:
        from sandbox_v1.domain.interfaces.container_manager import ContainerStatus

        container_status = await self._container_manager.check_container_status(sandbox.container_id)
        return container_status == ContainerStatus.RUNNING
