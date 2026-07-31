from __future__ import annotations

import argparse
import asyncio
from pathlib import Path


async def parse_xlsx(
    file_path: str | Path,
    *,
    image_path: str | Path | None = None,
) -> str:
    file_path = Path(file_path)
    image_path = Path(image_path) if image_path else file_path.parent / "images"
    return await asyncio.to_thread(_parse_xlsx, file_path, image_path)


def _parse_xlsx(file_path: Path, image_path: Path) -> str:
    from mineru.backend.office.office_middle_json_mkcontent import union_make
    from mineru.backend.office.xlsx_analyze import office_xlsx_analyze
    from mineru.data.data_reader_writer import FileBasedDataWriter
    from mineru.utils.enum_class import MakeMode

    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    middle_json, _ = office_xlsx_analyze(
        file_path.read_bytes(),
        image_writer=FileBasedDataWriter(str(image_path)),
    )
    _convert_simple_mineru_tables(middle_json)
    return union_make(
        middle_json["pdf_info"],
        MakeMode.MM_MD,
        image_path.name,
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


def fast_parse_xlsx(file_path: str | Path) -> str:
    import pandas as pd

    file_path = Path(file_path)
    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    with pd.ExcelFile(file_path, engine="openpyxl") as workbook:
        sheet_frames = pd.read_excel(
            workbook,
            sheet_name=None,
            dtype=str,
            keep_default_na=False,
            na_filter=False,
        )

    sections = []
    for sheet_name, frame in sheet_frames.items():
        markdown = str(frame.fillna("").to_markdown(index=False) or "").strip()
        sections.append(f"# Sheet: {sheet_name}\n\n{markdown}".rstrip())

    return "\n\n".join(sections)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse an XLSX into Markdown.")
    parser.add_argument("file_path", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fast", action="store_true")
    args = parser.parse_args()

    if args.fast:
        markdown = fast_parse_xlsx(args.file_path)
    else:
        markdown = asyncio.run(
            parse_xlsx(
                args.file_path,
                image_path=args.output.parent / "images",
            )
        )

    args.output.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {args.output}")


if __name__ == "__main__":
    main()
