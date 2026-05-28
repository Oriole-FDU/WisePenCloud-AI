from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ExecuteRequestDTO:
    package_id: str
    entry: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    timeout_ms: Optional[int] = None
    limits: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "ExecuteRequestDTO":
        package_id = str(data.get("package_id") or "").strip()
        entry = data.get("entry")
        args = data.get("args")
        env = data.get("env")
        timeout_ms = data.get("timeout_ms")
        limits = data.get("limits")

        return cls(
            package_id=package_id,
            entry=str(entry).strip() if isinstance(entry, str) and entry.strip() else None,
            args=[str(a) for a in args] if isinstance(args, list) else [],
            env={str(k): str(v) for k, v in env.items()} if isinstance(env, dict) else {},
            timeout_ms=int(timeout_ms) if isinstance(timeout_ms, int) else None,
            limits=limits if isinstance(limits, dict) else {},
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "entry": self.entry,
            "args": list(self.args),
            "env": dict(self.env),
            "timeout_ms": self.timeout_ms,
            "limits": dict(self.limits),
        }


@dataclass(frozen=True)
class ExecuteResponseDTO:
    request_id: str
    status: str
    sandbox_id: Optional[str] = None
    exit_code: Optional[int] = None
    stdout: str = ""
    stderr: str = ""
    duration_ms: Optional[int] = None
    artifacts: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "request_id": self.request_id,
            "status": self.status,
            "sandbox_id": self.sandbox_id,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "duration_ms": self.duration_ms,
            "artifacts": list(self.artifacts),
        }

