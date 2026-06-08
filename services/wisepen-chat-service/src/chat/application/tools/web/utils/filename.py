import re
from pathlib import PurePosixPath

# 定义高危可执行文件内层后缀黑名单
_DANGEROUS_FILENAME_SUFFIXES = frozenset({
    ".bat",
    ".cmd",
    ".com",
    ".dll",
    ".exe",
    ".jar",
    ".js",
    ".msi",
    ".ps1",
    ".scr",
    ".sh",
    ".vbs",
})


def sanitize_download_filename(name: str) -> str:
    """
    清洗不可信的用户输入文件名，去除路径前缀、控制字符，防范路径穿越攻击并提供安全兜底。

    Args:
    - name (str): 待清洗的原始文件名（可能包含路径、特殊控制字符或为空值）。

    Return:
    - str: 清洗后的安全纯文件名。如果文件名完全非法或为空，则返回默认的 "download"。
    """
    normalized = name.replace("\\", "/")
    base = PurePosixPath(normalized).name.strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base).strip()

    if base in {"", ".", "..", "~"}:
        return "download"

    return base


def drop_dangerous_inner_suffix(name: str) -> str:
    """
    检测文件名的多重后缀，若发现内层后缀属于高危可执行文件（如 .exe, .sh, .bat 等），则自动将其剔除，以防范欺骗性的伪装后缀攻击。

    Args:
    - name (str): 已经被清洗过的纯文件名（不含路径）。

    Return:
    - str: 剔除高危内层后缀后的安全文件名；如果内层后缀安全，则原样返回。
    """
    path_name = PurePosixPath(name)
    supported_suffix = path_name.suffix
    stem_path = PurePosixPath(path_name.stem)

    if stem_path.suffix.lower() not in _DANGEROUS_FILENAME_SUFFIXES:
        return path_name.name

    safe_stem = stem_path.stem or path_name.stem
    return f"{safe_stem}{supported_suffix}"
