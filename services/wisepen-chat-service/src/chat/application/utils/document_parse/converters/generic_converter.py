from __future__ import annotations

import asyncio
from pathlib import Path

from ..errors import UnsupportedDocumentFormatError
from .base import get_markitdown


class GenericConverter:
    """使用 MarkItDown 转换没有专用转换器的文档。"""

    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        extension = (
            Path(file_name).suffix or file_path.suffix
        ).lower().lstrip(".")

        try:
            result = await asyncio.to_thread(
                get_markitdown().convert_local,
                file_path,
            )
            markdown = result.text_content
            if isinstance(markdown, str) and (markdown := markdown.strip()):
                return markdown
        except Exception as exc:
            raise UnsupportedDocumentFormatError(
                file_name=file_name,
                extension=extension,
                mime_type=mime_type,
            ) from exc

        raise UnsupportedDocumentFormatError(
            file_name=file_name,
            extension=extension,
            mime_type=mime_type,
        )
