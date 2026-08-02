from __future__ import annotations

import argparse
from pathlib import Path

from .converters.docx import DocxConverter


def parse_docx(
    file_path: str | Path,
    *,
    image_path: str | Path | None = None,
) -> str:
    file_path = Path(file_path)
    image_path = Path(image_path) if image_path is not None else None
    return DocxConverter().convert(file_path, image_path=image_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a DOCX into Markdown.")
    parser.add_argument("file_path", type=Path)
    args = parser.parse_args()
    output_path = args.file_path.with_suffix(".md")

    markdown = parse_docx(
        args.file_path,
        image_path=output_path.parent / "images",
    )

    output_path.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {output_path}")


if __name__ == "__main__":
    main()
