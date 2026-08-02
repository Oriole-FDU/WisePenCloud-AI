from __future__ import annotations

from dataclasses import dataclass
import re

from common.core.exceptions import ServiceException

from sandbox.domain.error_codes import SandboxErrorCode

_SEGMENT = re.compile(r"^[A-Za-z0-9_-]+$")
_DEFAULT_ROOT = "/workspace"
_LOGICAL_ROOT = "/workspace"


@dataclass(frozen=True)
class TenantScope:
    tenant_id: str
    workspace_id: str

    def __post_init__(self) -> None:
        if not _SEGMENT.fullmatch(self.tenant_id):
            raise ServiceException(SandboxErrorCode.WORKSPACE_PATH_INVALID, "租户标识非法")
        if not _SEGMENT.fullmatch(self.workspace_id):
            raise ServiceException(SandboxErrorCode.WORKSPACE_PATH_INVALID, "工作区标识非法")


class PathPolicy:
    """用户逻辑路径与 AIO 容器绝对路径之间的映射策略。"""

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
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "工作区根目录非法",
            )
        self._root = root
        # 开启隔离范围时，每个用户/会话落到 root/{tenant}/{workspace}，避免共享目录串写。
        self._scope_root = (
            f"{root}/{scope.tenant_id}/{scope.workspace_id}"
            if isolate_scope
            else root
        )

    @property
    def root(self) -> str:
        return self._scope_root

    def translate(self, path: str) -> str:
        # MCP/Chat 只公开 /workspace；真实容器目录是内部实现细节。
        value = (path or "").strip().replace("\\", "/")
        if not value:
            raise ServiceException(SandboxErrorCode.WORKSPACE_PATH_INVALID, "路径不能为空")
        if value == "~" or value.startswith("~/"):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "不支持 home 目录路径，请使用相对路径或 /workspace",
            )
        if value == _LOGICAL_ROOT or value == f"{_LOGICAL_ROOT}/":
            value = self._scope_root
        elif value.startswith(f"{_LOGICAL_ROOT}/"):
            value = f"{self._scope_root}/{value[len(_LOGICAL_ROOT) + 1:]}"
        elif value.startswith("/"):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "只支持 /workspace 下的逻辑绝对路径",
            )
        elif not value.startswith("/"):
            value = f"{self._scope_root}/{value}"
        if value != self._scope_root and not value.startswith(f"{self._scope_root}/"):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "不允许访问工作区外的绝对路径",
            )

        parts: list[str] = []
        for part in value.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ServiceException(
                    SandboxErrorCode.WORKSPACE_PATH_INVALID,
                    "不支持包含 .. 的工作区路径",
                )
            parts.append(part)
        resolved = "/" + "/".join(parts)
        if resolved != self._scope_root and not resolved.startswith(f"{self._scope_root}/"):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "拒绝访问工作区外路径",
            )
        return resolved

    def reverse(self, path: str) -> str:
        # 导出工作区时 AIO 返回容器绝对路径，这里反向确认它仍落在当前 scope 内。
        value = (path or "").replace("\\", "/")
        if not value.startswith("/"):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "工作区路径必须是绝对路径",
            )
        if any(part in ("", ".", "..") for part in value.split("/")[1:]):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "工作区路径非法",
            )
        if value == self._scope_root:
            return self._scope_root
        prefix = f"{self._scope_root}/"
        if not value.startswith(prefix):
            raise ServiceException(
                SandboxErrorCode.WORKSPACE_PATH_INVALID,
                "拒绝访问工作区外路径",
            )
        relative = value[len(prefix):]
        return f"{self._scope_root}/{relative}"
