# Main executor of scripts, should support various scripts including .py and .bat
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional

from sandbox.LifeSpan.sandboxLifespan import SandboxLimits
from sandbox.ScriptExecutor.scriptReader import ScriptPackage, ScriptSpec


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ExecutionContext:
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    trace_id: Optional[str] = None


@dataclass(frozen=True)
class ResultSinkSpec:
    kind: str
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SandboxSpec:
    provider: str = "docker"
    runtime: Optional[str] = None
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    env: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    context: ExecutionContext
    sandbox: SandboxSpec
    script: ScriptSpec
    script_package: ScriptPackage
    result_sink: Optional[ResultSinkSpec] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ExecutionArtifact:
    name: str
    uri: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class ExecutionResult:
    request_id: str
    status: ExecutionStatus
    sandbox_id: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: Optional[int] = None
    artifacts: Optional[List[ExecutionArtifact]] = None
    metadata: Optional[Dict[str, Any]] = None


class ExecutionRequestParser(ABC):
    @abstractmethod
    def parse(self, structured_request: Dict[str, Any], package: ScriptPackage) -> ExecutionRequest:
        raise NotImplementedError


class ScriptsExecutor:
    def __init__(self) -> None:
        pass

    def Execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        return self.Execute(request)

    def getInputScript(self, package: ScriptPackage) -> ScriptSpec:
        raise NotImplementedError

    def outputResult(self, result: ExecutionResult) -> str:
        raise NotImplementedError


class SandboxExecutionService(ABC):
    @abstractmethod
    async def execute(self, request: ExecutionRequest) -> ExecutionResult:
        raise NotImplementedError
