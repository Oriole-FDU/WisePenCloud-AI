from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Protocol

from docling.document_converter import DocumentConverter as DoclingConverter
from markitdown import MarkItDown


class DocumentConverter(Protocol):
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        ...


@lru_cache(maxsize=1)
def get_docling_converter() -> DoclingConverter:
    return DoclingConverter()


@lru_cache(maxsize=1)
def get_markitdown() -> MarkItDown:
    return MarkItDown()
