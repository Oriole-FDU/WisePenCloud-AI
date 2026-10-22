from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from sandbox.domain.entities import CommandTaskStatus
from sandbox.domain.interfaces.sandbox_file_provider import SandboxFileProvider


class SandboxProviderType(StrEnum):
    AIO = "AIO"


class ProviderCommandHandle(BaseModel):
    """Provider 内部命令句柄，不作为 MCP 对外 ID"""

    provider_session_id: str
    provider_command_id: str | None = None


class ProviderCommandSnapshot(BaseModel):
    """一次命令状态读取结果；stdout/stderr 是本次读取到的增量"""

    status: CommandTaskStatus
    stdout: str = ""
    stderr: str = ""
    stdout_offset: int = 0
    stderr_offset: int = 0
    exit_code: int | None = None


class SandboxProvision(BaseModel):
    """Provider 为一个沙箱实例提供的容器配置和运行时元数据"""

    image: str
    container_environment: dict[str, str] = Field(default_factory=dict)
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


class SandboxProvider(SandboxFileProvider, ABC):
    """沙箱生命周期、命令和显式文件能力的统一 Provider 扩展点"""

    @classmethod
    @abstractmethod
    def sandbox_provider_id(cls) -> SandboxProviderType:
        pass

    @classmethod
    @abstractmethod
    def from_config(cls, config: Mapping[str, Any]) -> SandboxProvider:
        pass

    @abstractmethod
    def create_provision(self) -> SandboxProvision:
        pass

    @abstractmethod
    async def check_ready(
        self,
        base_url: str | None,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> bool:
        pass

    @abstractmethod
    async def start_command(
        self,
        base_url: str,
        command: str,
        working_directory: str,
        timeout_seconds: int,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandHandle:
        pass

    @abstractmethod
    async def get_command_result(
        self,
        base_url: str,
        handle: ProviderCommandHandle,
        stdout_offset: int,
        stderr_offset: int,
        wait_timeout_seconds: float,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> ProviderCommandSnapshot:
        pass

    @abstractmethod
    async def kill_command(
        self,
        base_url: str,
        handle: ProviderCommandHandle,
        provider_metadata: Mapping[str, Any] | None = None,
    ) -> None:
        pass
