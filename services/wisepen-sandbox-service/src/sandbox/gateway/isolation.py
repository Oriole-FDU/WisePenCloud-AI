"""
Multi-tenant workspace isolation for Sandbox Gateway.

PathTranslator maps LLM-visible virtual paths (e.g. /workspace/main.py)
to tenant-scoped physical paths (e.g. /home/gem/workspaces/user_A/sess_1/main.py).

IMPORTANT: All paths are Unix paths (AIO runs in Linux container).
We use pure string operations instead of os.path to avoid Windows \ conversion.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from common.core.exceptions import ServiceException

SANDBOX_ROOT = "/home/gem/workspaces"
VIRTUAL_ROOT = "/workspace"
_ALLOWED_CHARS = re.compile(r"^[a-zA-Z0-9_-]+$")
SEP = "/"


def _normpath(p: str) -> str:
    """Normalize a Unix path using only forward-slash logic (no os.path)."""
    if not p:
        return "."
    parts = p.replace("\\", "/").split(SEP)
    result: list[str] = []
    for part in parts:
        if part in ("", "."):
            continue
        if part == "..":
            if result and result[-1] != "..":
                result.pop()
            else:
                result.append("..")
        else:
            result.append(part)
    if not result:
        return SEP if p.startswith(SEP) else "."
    normalized = SEP.join(result)
    if p.startswith(SEP):
        normalized = SEP + normalized
    return normalized


def _join(a: str, b: str) -> str:
    """Join two Unix path segments. b is relative, a may end with /."""
    a = a.rstrip(SEP)
    b = b.lstrip(SEP)
    if not b:
        return a
    return f"{a}{SEP}{b}"


class PathValidationError(ServiceException):
    """Raised when a path violates isolation rules. Inherits ServiceException."""

    def __init__(self, message: str, code=None):
        from common.sandbox import SandboxErrorCode
        super().__init__(code=code or SandboxErrorCode.PATH_TRAVERSAL, message=message)


@dataclass(frozen=True)
class TenantScope:
    user_id: str
    session_id: str

    @classmethod
    def from_security_context(cls) -> TenantScope:
        from common.security.context import SecurityContextHolder
        user_id = (SecurityContextHolder.get_user_id() or "").strip()
        session_id = (SecurityContextHolder.get_session_id() or "").strip()
        if not user_id:
            raise PathValidationError("missing X-User-Id")
        if not session_id:
            raise PathValidationError("missing X-Session-Id")
        if not _ALLOWED_CHARS.match(user_id):
            raise PathValidationError("invalid user_id characters")
        if not _ALLOWED_CHARS.match(session_id):
            raise PathValidationError("invalid session_id characters")
        return cls(user_id=user_id, session_id=session_id)


class PathTranslator:
    """
    Translates LLM-visible virtual paths to tenant-scoped physical paths.

    Rules:
    1. /workspace/xxx → SANDBOX_ROOT/{uid}/{sid}/xxx
    2. ~/xxx          → SANDBOX_ROOT/{uid}/{sid}/xxx
    3. xxx (relative) → SANDBOX_ROOT/{uid}/{sid}/xxx
    4. Any other absolute path → rejected
    5. Path traversal (..)      → rejected
    """

    def __init__(self, scope: TenantScope):
        self._scope = scope
        self._physical_root = _normpath(
            f"{SANDBOX_ROOT}/{scope.user_id}/{scope.session_id}"
        )

    @property
    def physical_root(self) -> str:
        return self._physical_root

    def translate(self, user_path: str) -> str:
        p = (user_path or "").strip()
        if not p:
            raise PathValidationError("empty path")

        # Rule 1: /workspace/xxx → tenant root
        if p.startswith(VIRTUAL_ROOT + "/") or p == VIRTUAL_ROOT:
            rel = _normpath(p[len(VIRTUAL_ROOT):]).lstrip(SEP)
            return self._resolve(rel)

        # Rule 2: ~/xxx → tenant root
        if p.startswith("~/"):
            rel = _normpath(p[2:]).lstrip(SEP)
            return self._resolve(rel)

        # Rule 3: relative path → tenant root
        if not p.startswith("/"):
            rel = _normpath(p).lstrip(SEP)
            return self._resolve(rel)

        # Rule 4: other absolute paths → rejected
        raise PathValidationError(
            f"access denied: absolute paths outside {VIRTUAL_ROOT} are not allowed"
        )

    def _resolve(self, relative: str) -> str:
        resolved = _normpath(_join(self._physical_root, relative)) if relative else self._physical_root
        parts = resolved.split(SEP)
        if ".." in parts:
            raise PathValidationError("path traversal denied")
        if not resolved.startswith(self._physical_root + SEP) and resolved != self._physical_root:
            raise PathValidationError("access outside workspace denied")
        return resolved

    def reverse(self, physical_path: str) -> str:
        """Convert a physical path back to a virtual path (for response sanitization)."""
        root = self._physical_root
        if physical_path.startswith(root + "/"):
            return VIRTUAL_ROOT + "/" + physical_path[len(root) + 1:]
        if physical_path == root:
            return VIRTUAL_ROOT
        return physical_path
