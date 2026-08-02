from __future__ import annotations

import argparse
from enum import StrEnum
from pathlib import Path

from .converters.xlsx import XlsxConverter


class XlsxBackend(StrEnum):
    OPENPYXL = "openpyxl"
    MINERU = "mineru"


def parse_xlsx(
    file_path: str | Path,
    *,
    image_path: str | Path | None = None,
    backend: XlsxBackend | str = XlsxBackend.OPENPYXL,
) -> str:
    file_path = Path(file_path)
    image_path = Path(image_path) if image_path is not None else None
    if not file_path.is_file():
        raise FileNotFoundError(file_path)

    backend = XlsxBackend(backend)
    if backend is XlsxBackend.OPENPYXL:
        return XlsxConverter().convert(file_path, image_path=image_path)

    from mineru.backend.office.office_middle_json_mkcontent import union_make
    from mineru.backend.office.xlsx_analyze import office_xlsx_analyze
    from mineru.data.data_reader_writer import FileBasedDataWriter
    from mineru.utils.enum_class import MakeMode

    middle_json, _ = office_xlsx_analyze(
        file_path.read_bytes(),
        image_writer=(
            FileBasedDataWriter(str(image_path))
            if image_path is not None
            else None
        ),
    )
    return union_make(
        middle_json["pdf_info"],
        MakeMode.MM_MD,
        image_path.name if image_path is not None else "",
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse an XLSX into Markdown.")
    parser.add_argument("file_path", type=Path)
    parser.add_argument(
        "--backend",
        choices=[item.value for item in XlsxBackend],
        default=XlsxBackend.OPENPYXL.value,
        help="XLSX parse backend. Defaults to openpyxl; mineru preserves MinerU HTML blocks.",
    )
    args = parser.parse_args()
    output_path = args.file_path.with_suffix(".md")

    markdown = parse_xlsx(
        args.file_path,
        image_path=output_path.parent / "images",
        backend=args.backend,
    )

    output_path.write_text(markdown, encoding="utf-8")
    print(f"Markdown saved to {output_path}")


if __name__ == "__main__":
    main()
