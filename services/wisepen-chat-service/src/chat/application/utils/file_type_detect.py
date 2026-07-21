from __future__ import annotations

from dataclasses import dataclass
from mimetypes import guess_type
from pathlib import Path

from magika import Magika


@dataclass(frozen=True, slots=True)
class FileType:
    label: str
    mime_type: str
    extension: str


_magika = Magika()


def detect_file_type(
    file_path: str | Path,
    *,
    fallback_name: str | None = None,
) -> FileType:
    path = Path(file_path)
    file_name = fallback_name or path.name
    try:
        result = _magika.identify_path(path)
    except Exception:
        return _fallback_file_type(file_name)

    if not result.ok:
        return _fallback_file_type(file_name)

    return FileType(
        label=result.output.label.lower(),
        mime_type=result.output.mime_type.lower(),
        extension=_extension_from_name(file_name),
    )


def detect_file_type_from_bytes(
    content: bytes,
    *,
    fallback_name: str | None = None,
) -> FileType:
    if not content:
        return _fallback_file_type(fallback_name)

    try:
        result = _magika.identify_bytes(content)
    except Exception:
        return _fallback_file_type(fallback_name)

    if not result.ok:
        return _fallback_file_type(fallback_name)

    return FileType(
        label=result.output.label.lower(),
        mime_type=result.output.mime_type.lower(),
        extension=_extension_from_name(fallback_name),
    )


def _fallback_file_type(name: str | None) -> FileType:
    extension = _extension_from_name(name)
    return FileType(
        label=extension,
        mime_type=(guess_type(name or "")[0] or "").lower(),
        extension=extension,
    )


def _extension_from_name(name: str | None) -> str:
    if not name:
        return ""

    return Path(name).suffix.lower().lstrip(".")
