from __future__ import annotations

import re
from pathlib import PurePosixPath

_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")
_MAX_FILENAME_LENGTH = 180
_DANGEROUS_INNER_SUFFIXES = frozenset({
    ".bat", ".cmd", ".com", ".dll", ".exe",
    ".jar", ".js", ".msi", ".ps1", ".scr", ".sh", ".vbs",
})


def sanitize_tool_file_name(filename: str, *, default: str = "file") -> str:
    """将任意文件名清理为对文件系统和下载均安全的形式。

    Args:
        filename: 原始文件名；可能来自工具输出、URL 推断名或上游元数据。
        default: 清洗后为空时使用的默认文件名。

    Returns:
        已移除路径片段、危险字符和危险内层后缀的安全文件名。
    """
    base = PurePosixPath(str(filename).replace("\\", "/")).name.strip()
    if not base:
        return default

    path = PurePosixPath(base)
    suffix = _SAFE_FILENAME_PATTERN.sub("", path.suffix).lower()
    stem = path.stem or default
    stem_path = PurePosixPath(stem)
    # 防双重扩展名伪装，例如 report.exe.pdf 最终会变成 report.pdf。
    if stem_path.suffix.lower() in _DANGEROUS_INNER_SUFFIXES:
        stem = stem_path.stem or default

    safe_stem = _SAFE_FILENAME_PATTERN.sub("_", stem).strip("._-") or default
    safe = f"{safe_stem}{suffix}"
    return safe[:_MAX_FILENAME_LENGTH] or default
