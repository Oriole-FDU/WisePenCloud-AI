from __future__ import annotations

from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path

from magika import Magika


@dataclass(frozen=True, slots=True)
class FileType:
    label: str
    mime_type: str


_magika = Magika()


def detect_file_type(file_path: str | Path) -> FileType:
    path = Path(file_path)
    try:
        result = _magika.identify_path(path)
    except Exception:
        return _fallback_file_type(path)
    if not result.ok:
        return _fallback_file_type(path)
    return FileType(
        label=result.output.label.lower(),
        mime_type=result.output.mime_type.lower(),
    )


def detect_file_type_from_bytes(content: bytes, *, fallback_name: str | None = None) -> FileType:
    """基于字节内容检测文件类型。

    用于流式抓取时对 sniff buffer 做类型嗅探，无需完整落盘。

    Args:
        content: 文件内容字节（前 8KB~64KB 即可，magika 不需要完整文件）。
        fallback_name: 检测失败时用于推断 MIME 的文件名（含扩展名），可为 None。

    Returns:
        FileType: 检测结果，label 和 mime_type 均为小写。
    """
    if not content:
        return _fallback_file_type_from_name(fallback_name)
    try:
        result = _magika.identify_bytes(content)
    except Exception:
        return _fallback_file_type_from_name(fallback_name)
    if not result.ok:
        return _fallback_file_type_from_name(fallback_name)
    return FileType(
        label=result.output.label.lower(),
        mime_type=result.output.mime_type.lower(),
    )


def detect_mime_type(file_path: str | Path) -> str:
    return detect_file_type(file_path).mime_type


def _fallback_file_type(path: Path) -> FileType:
    mime_type = (guess_type(path.name)[0] or "").lower()
    return FileType(
        label=path.suffix.lower().lstrip("."),
        mime_type=mime_type,
    )


def _fallback_file_type_from_name(name: str | None) -> FileType:
    """字节检测失败时，按文件名扩展名兜底推断。"""
    if not name:
        return FileType(label="", mime_type="")
    mime_type = (guess_type(name)[0] or "").lower()
    suffix = Path(name).suffix.lower().lstrip(".")
    return FileType(label=suffix, mime_type=mime_type)
