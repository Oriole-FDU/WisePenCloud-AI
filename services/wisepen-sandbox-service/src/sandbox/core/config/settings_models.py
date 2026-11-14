from common.core.config.app_settings import (
    ConfigModel,
    InternalSecuritySettings,
    RpcSettings,
    ServiceDiscoverySettings,
)
from pydantic import Field, model_validator
from sandbox.domain.interfaces import SandboxProviderType


class RedisSettings(ConfigModel):
    url: str = Field(min_length=1)


class MongoDbSettings(ConfigModel):
    url: str = Field(min_length=1)
    database: str = Field(min_length=1)


class PersistenceSettings(ConfigModel):
    redis: RedisSettings
    mongodb: MongoDbSettings


class AioProviderSettings(ConfigModel):
    image: str = Field(min_length=1)
    request_timeout_seconds: float = Field(default=5.0, gt=0)
    file_transfer_timeout_seconds: float = Field(default=120.0, gt=0)
    derive_image: bool = Field(
        default=True,
        description="预热前是否从基础镜像派生一层自定义镜像（false 时直接使用基础镜像）",
    )

    @model_validator(mode="after")
    def validate_image_tag(self) -> "AioProviderSettings":
        # 基础镜像不能使用 latest 版本

        # 使用 digest 引用（repo@sha256:...），合法
        if "@" in self.image:
            return self
        # 最后一个 '/' 之后的 tag（避开 registry 端口里的冒号）非空且非 latest，合法
        last_slash = self.image.rfind("/")
        last_colon = self.image.rfind(":")
        tag = self.image[last_colon + 1 :] if last_colon > last_slash else ""
        # 没有 tag 或 tag 是 latest，报错
        if not tag or tag == "latest":
            raise ValueError(
                "sandbox provider image must pin a concrete version tag or digest, not 'latest'"
            )
        return self


class ProviderSettings(ConfigModel):
    active_id: SandboxProviderType
    settings: dict[SandboxProviderType, AioProviderSettings]

    @model_validator(mode="after")
    def validate_active_provider(self) -> "ProviderSettings":
        if self.active_id not in self.settings:
            raise ValueError("active-id must have a matching provider settings entry")
        return self


class WarmupSettings(ConfigModel):
    timeout_seconds: float = Field(gt=0)
    max_retries: int = Field(ge=1)


class DestroySettings(ConfigModel):
    timeout_seconds: float = Field(gt=0)


class PoolSettings(ConfigModel):
    target_ready: int = Field(default=5, ge=0)
    ready_max_age_seconds: float = Field(default=21600, gt=0)
    warmup: WarmupSettings
    destroy: DestroySettings
    watcher_interval_seconds: float = Field(gt=0)
    idle_timeout_seconds: float = Field(default=1800, gt=0)


class DockerSettings(ConfigModel):
    seccomp_profile: str = Field(default="unconfined", min_length=1)
    shm_size: str = Field(default="2g", min_length=1)


class CpuProtectionSettings(ConfigModel):
    sample_seconds: float = Field(default=1.0, gt=0, le=10)
    high_watermark_percent: float = Field(default=90.0, gt=0, le=100)
    low_watermark_percent: float = Field(default=75.0, ge=0, lt=100)

    @model_validator(mode="after")
    def validate_watermarks(self) -> "CpuProtectionSettings":
        if self.low_watermark_percent >= self.high_watermark_percent:
            raise ValueError(
                "low-watermark-percent must be lower than high-watermark-percent"
            )
        return self


class DiskProtectionSettings(ConfigModel):
    high_watermark_percent: float = Field(default=85.0, gt=0, lt=100)
    low_watermark_percent: float = Field(default=80.0, ge=0, lt=100)
    min_free_bytes: int = Field(default=50 * 1024 * 1024 * 1024, ge=0)

    @model_validator(mode="after")
    def validate_watermarks(self) -> "DiskProtectionSettings":
        if self.low_watermark_percent >= self.high_watermark_percent:
            raise ValueError(
                "low-watermark-percent must be lower than high-watermark-percent"
            )
        return self


class ResourceProtectionSettings(ConfigModel):
    host_container_storage_path: str = Field(
        default="/var/lib/containerd", min_length=1
    )
    probe_image: str = Field(default="busybox:1.37", min_length=1)
    cpu: CpuProtectionSettings = Field(default_factory=CpuProtectionSettings)
    disk: DiskProtectionSettings = Field(default_factory=DiskProtectionSettings)


class CommandSettings(ConfigModel):
    output_max_bytes: int = Field(default=65536, ge=1)


class FileTransferSettings(ConfigModel):
    max_bytes: int = Field(default=100 * 1024 * 1024, ge=1)
    spool_max_memory_bytes: int = Field(default=8 * 1024 * 1024, ge=1)
    download_url_duration_seconds: int = Field(default=900, ge=1)
    timeout_seconds: float = Field(default=120.0, gt=0)


class WorkspaceSettings(ConfigModel):
    container_root: str = Field(default="./data/workspaces", min_length=1)
    export_root: str = Field(default="./data/workspace-cache", min_length=1)


class SandboxSettings(ConfigModel):
    security: InternalSecuritySettings
    persistence: PersistenceSettings
    provider: ProviderSettings
    pool: PoolSettings
    docker: DockerSettings = Field(default_factory=DockerSettings)
    resource_protection: ResourceProtectionSettings = Field(
        default_factory=ResourceProtectionSettings
    )
    command: CommandSettings = Field(default_factory=CommandSettings)
    file_transfer: FileTransferSettings = Field(default_factory=FileTransferSettings)
    rpc: RpcSettings = Field(default_factory=RpcSettings)
    service_discovery: ServiceDiscoverySettings = Field(
        default_factory=ServiceDiscoverySettings
    )
    workspace: WorkspaceSettings = Field(default_factory=WorkspaceSettings)
