import re
import tempfile
from pathlib import Path

_TEMP_DIR_NAME = "wisepen-chat-upload-files"

_UUID_HEX_LENGTH = 32
_UUID_SEPARATOR_LENGTH = 1
_UUID_PREFIX_TOTAL_LENGTH = _UUID_HEX_LENGTH + _UUID_SEPARATOR_LENGTH

_PATH_SEGMENT_MAX_LENGTH = 120

_FALLBACK_STEM = "document"

_HEX_DIGITS = frozenset("0123456789abcdef")

_SAFE_CHAR_PATTERN = re.compile(r"[^\w.\- \u4e00-\u9fff]")

_WINDOWS_RESERVED_NAMES = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


def document_export_output_path() -> Path:
    """获取文档导出临时文件根目录，按 user/session 隔离。"""
    return Path(tempfile.gettempdir()) / _TEMP_DIR_NAME


def sanitize_path_segment(value: str, *, fallback: str = _FALLBACK_STEM) -> str:
    """清洗路径片段，移除路径危险字符，降级为 fallback 值（若清洗后为空）。"""
    if not value:
        return fallback

    cleaned = value.strip().strip(". ").replace("/", "_").replace("\\", "_")
    if cleaned in (".", "..", ""):
        return fallback

    if ":" in cleaned:
        prefix = cleaned.split(":")[0]
        if len(prefix) == 1 and prefix.isalpha():
            cleaned = cleaned.split(":", 1)[1].lstrip("\\/")

    cleaned = _SAFE_CHAR_PATTERN.sub("_", cleaned).strip(". ")
    if not cleaned:
        return fallback

    base_name = cleaned.rsplit(".", 1)[0] if "." in cleaned else cleaned
    if base_name.upper() in _WINDOWS_RESERVED_NAMES:
        cleaned += "_"

    return cleaned[:_PATH_SEGMENT_MAX_LENGTH]


def is_path_within_root(path: Path, root: Path) -> bool:
    """检查解析后的路径是否严格位于根目录内，防止路径穿越。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except (ValueError, OSError):
        return False


def display_file_name(*, storage_file_name: str) -> str:
    """从存储文件名（含 uuid 前缀）提取展示给用户的下载文件名。

    存储文件名格式：<uuid_hex>-<safe_stem><suffix>
    """
    prefix, sep, rest = storage_file_name.partition("-")
    is_uuid_prefix = (
        sep
        and len(prefix) == _UUID_HEX_LENGTH
        and all(c in _HEX_DIGITS for c in prefix)
    )
    if is_uuid_prefix:
        return rest or storage_file_name
    return storage_file_name


def storage_stem_for_download_ref(*, safe_stem: str, suffix: str) -> str:
    """生成适合作为下载引用的存储 stem，预留 uuid 前缀空间，截断过长名称。"""
    max_stem_length = _PATH_SEGMENT_MAX_LENGTH - _UUID_PREFIX_TOTAL_LENGTH - len(suffix)
    if max_stem_length <= 0:
        return _FALLBACK_STEM
    return safe_stem[:max_stem_length] or _FALLBACK_STEM
