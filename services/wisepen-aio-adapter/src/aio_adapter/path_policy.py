from __future__ import annotations

from dataclasses import dataclass
import re


class PathPolicyError(ValueError):
    pass


_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_ROOT = "/workspace"


@dataclass(frozen=True)
class TenantScope:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not _SEGMENT.fullmatch(self.tenant_id):
            raise PathPolicyError("invalid tenant id")
        if not _SEGMENT.fullmatch(self.workspace_id):
            raise PathPolicyError("invalid workspace id")


class PathPolicy:
    def __init__(self, scope: TenantScope) -> None:
        self._scope = scope

    def translate(self, path: str) -> str:
        value = (path or "").strip().replace("\\", "/")
        if not value:
            raise PathPolicyError("empty path")
        if value == "~":
            value = _ROOT
        elif value.startswith("~/"):
            value = f"{_ROOT}/{value[2:]}"
        elif not value.startswith("/"):
            value = f"{_ROOT}/{value}"
        if value != _ROOT and not value.startswith(f"{_ROOT}/"):
            raise PathPolicyError("absolute paths outside workspace are not allowed")

        parts: list[str] = []
        for part in value.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                if parts:
                    parts.pop()
                else:
                    raise PathPolicyError("path traversal denied")
                continue
            parts.append(part)
        resolved = "/" + "/".join(parts)
        if resolved != _ROOT and not resolved.startswith(f"{_ROOT}/"):
            raise PathPolicyError("path outside workspace denied")
        return resolved

    def reverse(self, path: str) -> str:
        value = (path or "").replace("\\", "/")
        if any(part in ("", ".", "..") for part in value.split("/")):
            raise PathPolicyError("invalid workspace path")
        if value == _ROOT:
            return _ROOT
        prefix = f"{_ROOT}/"
        if not value.startswith(prefix):
            raise PathPolicyError("path outside workspace denied")
        relative = value[len(prefix):]
        return f"{_ROOT}/{relative}"
