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
    args = parser.parse_args()
    output_path = Path(args.file_path).with_suffix(".md")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    markdown = asyncio.run(parse_image(args.file_path))
    output_path.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {output_path}")


if __name__ == "__main__":
    main()
