from __future__ import annotations

import asyncio

import pdf_inspector

from ..core.errors import UrlFetchError


async def extract_pdf_markdown(
    content: bytes,
    *,
    url: str,
) -> str:
    """将原生文本 PDF 快速转换为带页码标记的 Markdown。"""
    try:
        result = await asyncio.to_thread(
            pdf_inspector.extract_pages_markdown_bytes,
            content,
        )
    except Exception as exc:
        raise UrlFetchError(
            url=url,
            reason=f"PDF extraction failed: {exc}",
        ) from exc

    pages = [
        (
            f"<!-- page {page.page + 1} -->\n\n"
            f"{page.markdown.strip()}"
        )
        for page in result.pages
        if page.markdown.strip()
    ]

    if not pages:
        raise UrlFetchError(
            url=url,
            reason="PDF contains no extractable markdown",
        )

    return "\n\n".join(pages)