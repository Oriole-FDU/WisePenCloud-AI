from __future__ import annotations

import asyncio
from pathlib import Path

from docling_core.types.doc import ImageRefMode

from ..errors import DocumentParserError
from .base import get_docling_converter


class OfficeConverter:
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        try:
            result = await asyncio.to_thread(
                get_docling_converter().convert,
                file_path,
            )
        except Exception as exc:
            raise DocumentParserError(
                f"Failed to convert Office document {file_name}."
            ) from exc

        return result.document.export_to_markdown(
            image_mode=ImageRefMode.EMBEDDED,
            traverse_pictures=True,
        ).strip()
