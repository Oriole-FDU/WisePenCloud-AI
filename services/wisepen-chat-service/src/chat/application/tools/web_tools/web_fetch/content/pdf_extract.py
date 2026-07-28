from __future__ import annotations

import asyncio

import pdf_inspector

from ..core.errors import UrlFetchError


async def extract_pdf_markdown(
    content: bytes,
    *,
    url: str,
) -> str:
    """在线程池中将 PDF 转换为 Markdown。"""
    try:
        result = await asyncio.to_thread(
            pdf_inspector.process_pdf_bytes,
            content,
        )
    except Exception as exc:
        raise UrlFetchError(
            url=url,
            reason=f"PDF extraction failed: {exc}",
        ) from exc

    if not result.markdown:
        raise UrlFetchError(
            url=url,
            reason="PDF contains no extractable markdown",
        )

    return result.markdown
