from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .converters.image import get_image_converter


async def parse_image(
    file_path: str | Path,
) -> str:
    return await get_image_converter().convert(file_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse an image into Markdown.")
    parser.add_argument("file_path", help="Local image path or image URL")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)

    markdown = asyncio.run(parse_image(args.file_path))
    args.output.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {args.output}")


if __name__ == "__main__":
    main()
