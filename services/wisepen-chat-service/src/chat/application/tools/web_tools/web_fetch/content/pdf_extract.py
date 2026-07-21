from __future__ import annotations

import asyncio
import re

import pypdfium2 as pdfium
import unicodedata

from ..core.errors import UrlFetchError

# PDF 提取常见空白字符，统一替换为空格。
_UNICODE_SPACES = (
    "\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006"
    "\u2007\u2008\u2009\u200a\u202f\u205f\u3000"
)

# Unicode 字符归一化表：
# - 特殊空白统一处理；
# - 隐藏字符删除；
# - PDF 常见连字展开。
_UNICODE_TRANSLATION_TABLE = str.maketrans(
    {
        **{char: " " for char in _UNICODE_SPACES},

        "\u0085": "\n",
        "\u2028": "\n",
        "\u2029": "\n",

        # 隐藏字符
        "\u00ad": None,
        "\u200b": None,
        "\u200c": None,
        "\u200d": None,
        "\u2060": None,
        "\ufeff": None,
        "\ufffe": None,
        "\uffff": None,

        # PDF 常见连字
        "\ufb00": "ff",
        "\ufb01": "fi",
        "\ufb02": "fl",
        "\ufb03": "ffi",
        "\ufb04": "ffl",
        "\ufb05": "st",
        "\ufb06": "st",
    }
)

# 删除不可见控制字符，但保留换行和常见文本字符。
_CONTROL_CHAR_TRANSLATION_TABLE = {
    codepoint: None
    for codepoint in (
        *range(0x00, 0x09),
        0x0B,
        0x0C,
        *range(0x0E, 0x20),
        *range(0x7F, 0xA0),
    )
}

_TRAILING_WHITESPACE_RE = re.compile(r"[^\S\n]+$",re.MULTILINE)

_EXCESS_BLANK_LINES_RE = re.compile( r"\n{3,}")


async def extract_pdf_text(
        content: bytes,
        *,
        url: str,
) -> str:
    """在线程池中使用 PDFium 快速提取纯文本。"""
    try:
        text = await asyncio.to_thread(
            _extract_pdf_text_sync,
            content,
        )
    except Exception as exc:
        raise UrlFetchError(
            url=url,
            reason=f"PDF extraction failed: {exc}",
        ) from exc

    if not text:
        raise UrlFetchError(
            url=url,
            reason="PDF contains no extractable text",
        )

    return text


def _extract_pdf_text_sync(content: bytes) -> str:
    """同步执行 PDFium 文本提取。"""
    document = pdfium.PdfDocument(content)
    page_texts: list[str] = []

    try:
        for page_index in range(len(document)):
            page = document[page_index]
            text_page = None

            try:
                text_page = page.get_textpage()
                text = _clean_pdf_text(
                    text_page.get_text_bounded(
                        errors="replace",
                    )
                )

                if text:
                    page_texts.append(text)

            finally:
                if text_page is not None:
                    text_page.close()

                page.close()

    finally:
        document.close()

    text = "\n\n".join(page_texts)
    return f"{text}\n" if text else ""


def _clean_pdf_text(text: str) -> str:
    """清理 PDF 文本中的编码和格式噪声。"""
    text = text.replace("\r\n","\n",).replace("\r","\n")
    text = text.translate(_UNICODE_TRANSLATION_TABLE)
    text = text.translate(_CONTROL_CHAR_TRANSLATION_TABLE)
    text = unicodedata.normalize( "NFC", text)
    text = _TRAILING_WHITESPACE_RE.sub( "", text)

    return _EXCESS_BLANK_LINES_RE.sub("\n\n", text).strip()
