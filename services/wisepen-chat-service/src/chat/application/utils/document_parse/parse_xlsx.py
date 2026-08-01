from __future__ import annotations

import argparse
from pathlib import Path


def parse_xlsx(
    file_path: str | Path,
    *,
    image_path: str | Path | None = None,
) -> str:
    file_path = Path(file_path)
    image_path = Path(image_path) if image_path is not None else None

    from mineru.backend.office.office_middle_json_mkcontent import union_make
    from mineru.backend.office.xlsx_analyze import office_xlsx_analyze
    from mineru.data.data_reader_writer import FileBasedDataWriter
    from mineru.utils.enum_class import MakeMode

    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    middle_json, _ = office_xlsx_analyze(
        file_path.read_bytes(),
        image_writer=(
            FileBasedDataWriter(str(image_path))
            if image_path is not None
            else None
        ),
    )
    _convert_simple_mineru_tables(middle_json)
    return union_make(
        middle_json["pdf_info"],
        MakeMode.MM_MD,
        image_path.name if image_path is not None else "",
    )


def _convert_simple_mineru_tables(middle_json: dict) -> None:
    """将不依赖 HTML 结构或 MinerU 后处理的简单表格转为 pipe table
    减少 token 消耗。
    """
    from bs4 import BeautifulSoup
    from markdownify import markdownify

    for page_info in middle_json["pdf_info"]:
        for para_block in page_info.get("para_blocks", []):
            for body_block in para_block.get("blocks", []):
                for line in body_block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("type") != "table":
                            continue
                        html = span.get("html")
                        if not isinstance(html, str) or not html.strip():
                            continue

                        table = BeautifulSoup(html, "html.parser").find("table")
                        if (
                            table is None
                            or table.find(("img", "eq")) is not None
                            or any(
                                cell.has_attr("rowspan") or cell.has_attr("colspan")
                                for cell in table.find_all(("td", "th"))
                            )
                        ):
                            continue

                        span["html"] = markdownify(str(table)).strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse an XLSX into Markdown.")
    parser.add_argument("file_path", type=Path)
    args = parser.parse_args()
    output_path = args.file_path.with_suffix(".md")

    markdown = parse_xlsx(
        args.file_path,
        image_path=output_path.parent / "images",
    )

    output_path.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {output_path}")


if __name__ == "__main__":
    main()
