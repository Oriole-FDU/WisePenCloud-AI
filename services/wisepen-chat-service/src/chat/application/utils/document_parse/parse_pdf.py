from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

import pdf_inspector

from .converters.pdf import get_pdf_converter


async def parse_pdf(
    file_path: str | Path,
    *,
    api_url: str | None = None,
) -> str:
    """将指定 PDF 交给配置的 MinerU 服务解析。"""
    file_path = Path(file_path)
    if api_url is None:
        from chat.core.config.app_settings import settings

        api_url = settings.MINERU_API_URL

    return await get_pdf_converter(api_url).convert(
        file_path,
        file_name=file_path.name,
    )


def fast_parse_pdf(file_path: str | Path) -> str:
    file_path = Path(file_path)
    content = file_path.read_bytes()

    result = pdf_inspector.extract_pages_markdown_bytes(content)

    return "\n\n".join(
        f"<!-- page {page.page + 1} -->\n\n{page.markdown.strip()}"
        for page in result.pages
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a PDF into Markdown.")
    parser.add_argument("file_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()

    if args.fast:
        markdown = fast_parse_pdf(args.file_path)
    else:
        markdown = asyncio.run(parse_pdf(args.file_path))

    args.output.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {args.output}")


if __name__ == "__main__":
    main()
