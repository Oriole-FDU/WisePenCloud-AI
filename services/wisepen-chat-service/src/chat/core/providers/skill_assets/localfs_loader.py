from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from common.logger import log_event

from chat.domain.interfaces.skill_asset_loader import SkillAssetLoader
from chat.core.providers.skill_assets.oss_loader import OssSkillAssetLoader


class LocalFSSkillAssetLoader(SkillAssetLoader):
    """
    开发期 Skill 资产加载器

    先尝试 dev_fixtures 根目录下按 <skill_id>/<version>/<path> 的布局读文件
    若本地未命中且持有 OSS 回退加载器，转发到 OSS 从线上取（并由 OSS 加载器自己做磁盘缓存）

    生产环境不使用本类，container 里 DEV=False 会直接使用 OssSkillAssetLoader
    """

    # 约定前缀：与发布侧生成 object_key 同一套规则
    _OBJECT_KEY_PREFIX = "skills/"

    def __init__(
        self,
        root_dir: str,
        *,
        oss_fallback: Optional[OssSkillAssetLoader] = None,
    ) -> None:
        self._root = Path(root_dir).resolve()
        self._oss = oss_fallback

    async def start(self) -> None:
        if self._oss is not None:
            await self._oss.start()

    async def stop(self) -> None:
        if self._oss is not None:
            await self._oss.stop()

    async def load_by_object_key(self, object_key: str) -> bytes:
        parsed = self._parse_object_key(object_key)
        local_hit = self._try_read_local(parsed) if parsed is not None else None
        if local_hit is not None:
            return local_hit

        if self._oss is not None:
            log_event("Skill 资产 DEV 本地未命中，回退 OSS", object_key=object_key)
            return await self._oss.load_by_object_key(object_key)

        # 既无本地也无 OSS：给出清晰的失败语义
        if parsed is None:
            raise ValueError(f"object_key 不符合 skills/<skill_id>/<version>/<path> 约定: {object_key!r}")
        skill_id, version, path = parsed
        raise FileNotFoundError(f"Asset not found in local fixtures: {skill_id}/{version}/{path}")

    async def load_asset(self, skill_id: str, version: str, path: str) -> bytes:
        self._ensure_safe_segment(skill_id, kind="skill_id")
        self._ensure_safe_segment(version, kind="version")
        self._ensure_safe_rel_path(path)

        local_hit = self._try_read_local((skill_id, version, path))
        if local_hit is not None:
            return local_hit

        if self._oss is not None:
            log_event(
                "Skill 资产 DEV 本地未命中，回退 OSS",
                skill_id=skill_id,
                version=version,
                path=path,
            )
            return await self._oss.load_asset(skill_id=skill_id, version=version, path=path)

        raise FileNotFoundError(f"Asset not found: {skill_id}/{version}/{path}")

    # ---------- 内部实现 ----------

    def _parse_object_key(self, object_key: str) -> Optional[tuple[str, str, str]]:
        if not object_key or not object_key.startswith(self._OBJECT_KEY_PREFIX):
            return None
        rel = object_key[len(self._OBJECT_KEY_PREFIX):]
        parts = rel.split("/", 2)
        if len(parts) < 3 or not parts[2]:
            return None
        skill_id, version, path = parts
        try:
            self._ensure_safe_segment(skill_id, kind="skill_id")
            self._ensure_safe_segment(version, kind="version")
            self._ensure_safe_rel_path(path)
        except ValueError:
            return None
        return skill_id, version, path

    def _try_read_local(self, parsed: tuple[str, str, str]) -> Optional[bytes]:
        skill_id, version, path = parsed
        target_root = (self._root / skill_id / version).resolve()
        target = (target_root / path).resolve()
        if not str(target).startswith(str(target_root) + os.sep) and target != target_root:
            raise PermissionError(f"Asset path escapes skill asset root: {path}")
        if not target.is_file():
            return None
        # 按字节读：资产可能是文本也可能是二进制，解码是上游 Tool 的职责
        return target.read_bytes()

    @staticmethod
    def _ensure_safe_segment(segment: str, *, kind: str) -> None:
        if not segment:
            raise ValueError(f"{kind} must be non-empty")
        if "/" in segment or "\\" in segment or segment in (".", ".."):
            raise ValueError(f"Illegal {kind}: {segment!r}")

    @staticmethod
    def _ensure_safe_rel_path(rel_path: str) -> None:
        if not rel_path:
            raise ValueError("asset path must be non-empty")
        if "\\" in rel_path or rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError(f"Illegal asset path: {rel_path!r}")
