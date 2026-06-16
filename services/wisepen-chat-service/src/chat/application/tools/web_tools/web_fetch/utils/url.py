from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse


def filename_from_url(url: str) -> str | None:
    """从 URL 提取文件名，用于 fallback 文件类型检测或展示名。"""
    try:
        path = urlparse(url).path
        name = Path(path).name
        return name if name else None
    except Exception:
        return None
