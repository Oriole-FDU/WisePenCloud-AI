import os
from pathlib import Path

from chat.domain.interfaces.skill_asset_loader import SkillAssetLoader


class LocalFSSkillAssetLoader(SkillAssetLoader):
    """
    SkillAssetLoader 的本地文件系统实现。

    生产环境将被 OssSkillAssetLoader 替换；此实现只服务Q2开发阶段的本地开发与测试
    从一个约定根目录下按 <skill_id>/<version>/<path> 直接读文本。

    dev_fixtures 下的 asset 文件由 seeder 脚本预埋或开发者手工放置。
    """

    def __init__(self, root_dir: str) -> None:
        self._root = Path(root_dir).resolve()

    async def load_asset(self, skill_id: str, version: str, path: str) -> str:
        self._ensure_safe_segment(skill_id, kind="skill_id")
        self._ensure_safe_segment(version, kind="version")
        self._ensure_safe_rel_path(path)

        target_root = (self._root / skill_id / version).resolve()
        target = (target_root / path).resolve()

        # 路径逃逸防御：resolved 后必须仍在 target_root 下
        if not str(target).startswith(str(target_root) + os.sep) and target != target_root:
            raise PermissionError(f"Asset path escapes skill asset root: {path}")
        if not target.is_file():
            raise FileNotFoundError(f"Asset not found: {skill_id}/{version}/{path}")

        return target.read_text(encoding="utf-8")

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
        # POSIX 风格；禁止反斜杠 / 绝对路径 / .. 段
        if "\\" in rel_path or rel_path.startswith("/") or ".." in Path(rel_path).parts:
            raise ValueError(f"Illegal asset path: {rel_path!r}")
