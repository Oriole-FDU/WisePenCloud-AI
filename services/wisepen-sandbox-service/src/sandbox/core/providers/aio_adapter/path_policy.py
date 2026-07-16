from __future__ import annotations

from dataclasses import dataclass
import re


class PathPolicyError(ValueError):
    pass


_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_ROOT = "/workspace"


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
    def __init__(
        self,
        scope: TenantScope,
        root: str = _DEFAULT_ROOT,
        *,
        isolate_scope: bool = False,
    ) -> None:
        self._scope = scope
        root = (root or _DEFAULT_ROOT).rstrip("/")
        if not root.startswith("/") or ".." in root.split("/"):
            raise PathPolicyError("invalid workspace root")
        self._root = root
        self._scope_root = (
            f"{root}/{scope.tenant_id}/{scope.workspace_id}"
            if isolate_scope
            else root
        )

    @property
    def root(self) -> str:
        return self._scope_root

    def translate(self, path: str) -> str:
        value = (path or "").strip().replace("\\", "/")
        if not value:
            raise PathPolicyError("empty path")
        if value == "~":
            value = self._scope_root
        elif value.startswith("~/"):
            value = f"{self._scope_root}/{value[2:]}"
        elif not value.startswith("/"):
            value = f"{self._scope_root}/{value}"
        if value != self._scope_root and not value.startswith(f"{self._scope_root}/"):
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
        if resolved != self._scope_root and not resolved.startswith(f"{self._scope_root}/"):
            raise PathPolicyError("path outside workspace denied")
        return resolved

    def reverse(self, path: str) -> str:
        value = (path or "").replace("\\", "/")
        if not value.startswith("/"):
            raise PathPolicyError("workspace paths must be absolute")
        if any(part in ("", ".", "..") for part in value.split("/")[1:]):
            raise PathPolicyError("invalid workspace path")
        if value == self._scope_root:
            return self._scope_root
        prefix = f"{self._scope_root}/"
        if not value.startswith(prefix):
            raise PathPolicyError("path outside workspace denied")
        relative = value[len(prefix):]
        return f"{self._scope_root}/{relative}"
