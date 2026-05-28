# Create a new sandbox, lasting for a short time and remove unused ones
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, Type


class SandboxState(str, Enum):
    CREATING = "creating"
    RUNNING = "running"
    IDLE = "idle"
    DELETING = "deleting"
    FAILED = "failed"


@dataclass(frozen=True)
class SandboxLimits:
    cpu_cores: Optional[float] = None
    memory_mb: Optional[int] = None
    timeout_ms: Optional[int] = None
    disk_mb: Optional[int] = None
    pids_limit: Optional[int] = None
    network_enabled: Optional[bool] = None


@dataclass(frozen=True)
class SandboxCreateRequest:
    request_id: str
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    runtime: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxInfo:
    sandbox_id: str
    state: SandboxState
    created_at_epoch_ms: Optional[int] = None
    last_used_at_epoch_ms: Optional[int] = None
    workspace: Optional[str] = None
    runtime: Optional[str] = None
    provider: Optional[str] = None
    limits: Optional[SandboxLimits] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class SandboxProvider(ABC):
    @abstractmethod
    def create(self, request: SandboxCreateRequest) -> SandboxInfo: # Create a new sandbox
        raise NotImplementedError

    @abstractmethod
    def remove(self, sandbox_id: str) -> None: # Remove a sandbox
        raise NotImplementedError

    @abstractmethod
    def info(self, sandbox_id: str) -> SandboxInfo: # Get sandbox info
        raise NotImplementedError


class DockerSandboxProvider(SandboxProvider):
    pass


class SandboxFactory:
    def __init__(self) -> None:
        self._providers: Dict[str, Type[SandboxProvider]] = {}
        self._instances: Dict[str, SandboxProvider] = {}

    def register_provider(self, name: str, provider_cls: Type[SandboxProvider]) -> None:
        if not name:
            raise ValueError("provider name must be non-empty")
        self._providers[name] = provider_cls
        if name in self._instances:
            del self._instances[name]

    def get_provider(self, name: str) -> SandboxProvider:
        if name in self._instances:
            return self._instances[name]
        if name not in self._providers:
            raise KeyError(f"SandboxProvider '{name}' is not registered.")
        provider = self._providers[name]()
        self._instances[name] = provider
        return provider


class Sandbox:
    def __init__(self, factory: SandboxFactory, provider_name: str) -> None:
        self._factory = factory
        self._provider_name = provider_name

    def createSandbox(self, request: SandboxCreateRequest) -> SandboxInfo: # Create a new sandbox
        provider_name = request.metadata.get("provider") if request.metadata else None
        provider = self._factory.get_provider(provider_name or self._provider_name)
        return provider.create(request)

    def removeSandbox(self, sandbox_id: str) -> None: # Remove a sandbox
        provider = self._factory.get_provider(self._provider_name)
        provider.remove(sandbox_id)

    def getSandboxInfo(self, sandbox_id: str) -> SandboxInfo: # Get sandbox info
        provider = self._factory.get_provider(self._provider_name)
        return provider.info(sandbox_id)
