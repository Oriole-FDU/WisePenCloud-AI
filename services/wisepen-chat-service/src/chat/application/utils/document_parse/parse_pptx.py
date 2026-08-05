from __future__ import annotations

import argparse
from collections.abc import Mapping
from pathlib import Path


def parse_pptx(
    file_path: str | Path,
    *,
    image_path: str | Path | None = None,
) -> str:
    file_path = Path(file_path)
    image_path = Path(image_path) if image_path is not None else None

    from mineru.backend.office.office_middle_json_mkcontent import union_make
    from mineru.backend.office.pptx_analyze import office_pptx_analyze
    from mineru.data.data_reader_writer import FileBasedDataWriter
    from mineru.utils.enum_class import MakeMode

    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    middle_json, _ = office_pptx_analyze(
        file_path.read_bytes(),
        image_writer=(
            FileBasedDataWriter(str(image_path))
            if image_path is not None
            else None
        ),
    )

    pdf_info = middle_json.get("pdf_info")
    if not isinstance(pdf_info, list):
        raise ValueError("MinerU middle JSON must contain a pdf_info list.")

    pages: list[str] = []
    for index, page_info in enumerate(pdf_info):
        if not isinstance(page_info, Mapping):
            raise ValueError("MinerU pdf_info entries must be objects.")

        page_idx = page_info.get("page_idx", index)
        if type(page_idx) is not int or page_idx < 0:
            raise ValueError("MinerU page_idx must be a non-negative integer.")

        page_markdown = union_make(
            [page_info],
            MakeMode.MM_MD,
            image_path.name if image_path is not None else "",
        ).strip()
        marker = f"<!-- page {page_idx + 1} -->"
        pages.append(f"{marker}\n\n{page_markdown}" if page_markdown else marker)

    return "\n\n".join(pages)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a PPTX into Markdown.")
    parser.add_argument("file_path", type=Path)
    args = parser.parse_args()
    output_path = args.file_path.with_suffix(".md")

    markdown = parse_pptx(
        args.file_path,
        image_path=output_path.parent / "images",
    )

    output_path.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {output_path}")


if __name__ == "__main__":
    main()
