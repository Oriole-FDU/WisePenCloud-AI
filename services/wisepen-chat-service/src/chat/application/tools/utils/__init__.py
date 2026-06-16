from __future__ import annotations

from .file_type_detect import (
    FileType,
    detect_file_type,
    detect_file_type_from_bytes,
    detect_mime_type,
)

__all__ = [
    "FileType",
    "detect_file_type",
    "detect_file_type_from_bytes",
    "detect_mime_type",
]