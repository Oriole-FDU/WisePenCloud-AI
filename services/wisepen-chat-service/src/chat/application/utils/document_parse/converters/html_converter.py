from __future__ import annotations

import asyncio
from pathlib import Path

from ..errors import DocumentParserError
from .base import get_markitdown


class HtmlConverter:
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        try:
            result = await asyncio.to_thread(
                get_markitdown().convert_local,
                file_path,
            )
        except Exception as exc:
            raise DocumentParserError(
                f"Failed to convert HTML document {file_name}."
            ) from exc

        return str(result.text_content or "").strip()
