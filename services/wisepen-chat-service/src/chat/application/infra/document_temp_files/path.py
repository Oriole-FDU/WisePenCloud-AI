import re
from pathlib import Path, PurePosixPath

# 路径组件清洗：
# - 用于 user_id / session_id 这类目录名片段。
# - 只保留英文、数字、点、下划线、短横线。
_SAFE_COMPONENT_PATTERN = re.compile(r"[^A-Za-z0-9._-]+")

# 文件名清洗：
# - 用于用户可见下载文件名。
# - 允许中文，避免中文文件名被全部替换。
_SAFE_FILENAME_PATTERN = re.compile(r"[^A-Za-z0-9._\-\u4e00-\u9fff]+")

# 文件名最大长度：
# - 防止极长文件名造成文件系统兼容性问题。
_MAX_FILENAME_LENGTH = 180

# 危险内层后缀：
# - 防止类似 report.exe.pdf / script.js.txt 这种双后缀伪装。
# - 只剥离 stem 的最后一层危险后缀，最终文件后缀仍由调用方控制。
_DANGEROUS_INNER_SUFFIXES = frozenset(
    {
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
    }
)


def sanitize_document_filename(filename: str, *, default: str = "document") -> str:
    """
    清洗文档文件名，返回安全、可展示、可落盘的文件名。

    处理规则：
    - 去掉路径，只保留 basename。
    - 保留最终真实后缀。
    - 剥离危险内层后缀，防止双后缀伪装。
    - 清洗非法字符。
    - 限制最大文件名长度。
    """
    # 防止传入 ../a.pdf、C:\\a\\b.pdf 这类路径，只取最终文件名。
    base = PurePosixPath(str(filename).replace("\\", "/")).name.strip()
    if not base:
        return default

    path = PurePosixPath(base)
    suffix = path.suffix
    stem = path.stem or default

    # 处理双后缀伪装：
    # - report.exe.pdf -> report.pdf
    # - script.js.txt  -> script.txt
    stem_path = PurePosixPath(stem)
    if stem_path.suffix.lower() in _DANGEROUS_INNER_SUFFIXES:
        stem = stem_path.stem or default

    # stem 允许中文；suffix 只做字符清洗并统一小写。
    safe_stem = _SAFE_FILENAME_PATTERN.sub("_", stem).strip("._-") or default
    safe_suffix = _SAFE_FILENAME_PATTERN.sub("", suffix).lower()

    safe = f"{safe_stem}{safe_suffix}"
    return safe[:_MAX_FILENAME_LENGTH] or default


def session_root_for(
    *,
    temp_root: Path,
    user_id: str,
    session_id: str,
) -> Path:
    """
    构造用户会话级临时目录。

    目录结构：
    - temp_root / safe_user_id / safe_session_id

    作用：
    - 按 user_id / session_id 隔离临时文件。
    - 防止路径穿越。
    - 防止非法目录名污染文件系统。
    """
    # 只取 basename，防止 user_id / session_id 被构造成路径。
    raw_user_id = PurePosixPath(str(user_id).replace("\\", "/")).name
    raw_session_id = PurePosixPath(str(session_id).replace("\\", "/")).name

    # 目录名采用更严格的 ASCII 白名单，避免跨平台路径兼容问题。
    safe_user_id = _SAFE_COMPONENT_PATTERN.sub("_", raw_user_id).strip("._-") or "user"
    safe_session_id = (
        _SAFE_COMPONENT_PATTERN.sub("_", raw_session_id).strip("._-") or "session"
    )

    return temp_root / safe_user_id / safe_session_id