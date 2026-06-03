"""
Multi-tenant workspace isolation for AIO Gateway.

PathTranslator maps LLM-visible virtual paths (e.g. /workspace/main.py)
to tenant-scoped physical paths (e.g. /home/gem/workspaces/user_A/sess_1/main.py).
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

SANDBOX_ROOT = "/home/gem/workspaces"
VIRTUAL_ROOT = "/workspace"
_ALLOWED_CHARS = re.compile(r"^[a-zA-Z0-9_-]+$")


class PathValidationError(ValueError):
    """Raised when a path violates isolation rules. Does NOT leak physical paths."""


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
        self._physical_root = os.path.normpath(
            f"{SANDBOX_ROOT}/{scope.user_id}/{scope.session_id}"
        )

    @property
    def physical_root(self) -> str:
        return self._physical_root

    def translate(self, user_path: str) -> str:
        p = (user_path or "").strip()
        if not p:
            raise PathValidationError("empty path")

        # Rule 1: /workspace/... → tenant root
        if p.startswith(VIRTUAL_ROOT + "/") or p == VIRTUAL_ROOT:
            rel = os.path.normpath(p[len(VIRTUAL_ROOT):]).lstrip("/")
            return self._resolve(rel)

        # Rule 2: ~/... → tenant root
        if p.startswith("~/"):
            rel = os.path.normpath(p[2:]).lstrip("/")
            return self._resolve(rel)

        # Rule 3: relative path → tenant root
        if not p.startswith("/"):
            rel = os.path.normpath(p).lstrip("/")
            return self._resolve(rel)

        # Rule 4: other absolute paths → rejected
        raise PathValidationError(f"access denied: absolute paths outside {VIRTUAL_ROOT} are not allowed")

    def _resolve(self, relative: str) -> str:
        resolved = os.path.normpath(os.path.join(self._physical_root, relative))
        if ".." in resolved.split(os.sep):
            raise PathValidationError("path traversal denied")
        if not resolved.startswith(self._physical_root + os.sep) and resolved != self._physical_root:
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
