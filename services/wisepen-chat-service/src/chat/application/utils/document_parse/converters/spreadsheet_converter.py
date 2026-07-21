from __future__ import annotations

import asyncio
import csv
from io import StringIO
from pathlib import Path

import pandas as pd

from ..errors import DocumentParseError, DocumentParserError
from .utils import decode_text

_DELIMITED_MIME_TYPES = {"text/csv", "text/tab-separated-values"}


class SpreadsheetConverter:
    async def convert(
        self,
        file_path: Path,
        *,
        file_name: str,
        mime_type: str | None = None,
    ) -> str:
        try:
            return await asyncio.to_thread(
                self._convert,
                file_path,
                file_name,
                mime_type,
            )
        except DocumentParseError:
            raise
        except Exception as exc:
            raise DocumentParserError(
                f"Failed to parse spreadsheet {file_name}: {exc}."
            ) from exc

    @staticmethod
    def _convert(
        file_path: Path,
        file_name: str,
        mime_type: str | None,
    ) -> str:
        suffix = Path(file_name).suffix.lower() or file_path.suffix.lower()
        mime_type = (mime_type or "").partition(";")[0].strip().lower()

        if suffix in {".csv", ".tsv"} or mime_type in _DELIMITED_MIME_TYPES:
            text = decode_text(file_path.read_bytes(), file_name=file_name)

            if suffix == ".tsv" or mime_type == "text/tab-separated-values":
                delimiter = "\t"
            else:
                try:
                    delimiter = csv.Sniffer().sniff(
                        text[:8_192],
                        delimiters=",;|\t",
                    ).delimiter
                except csv.Error:
                    delimiter = ","

            frame = pd.read_csv(
                StringIO(text),
                sep=delimiter,
                dtype=str,
                keep_default_na=False,
                na_filter=False,
            )
            return _render_sheet_frames({file_name: frame})

        engine = "xlrd" if suffix == ".xls" else "openpyxl"
        with pd.ExcelFile(file_path, engine=engine) as workbook:
            sheet_frames = pd.read_excel(
                workbook,
                sheet_name=None,
                dtype=str,
                keep_default_na=False,
                na_filter=False,
            )

        return _render_sheet_frames(sheet_frames)


def _render_sheet_frames(
    sheet_frames: dict[str, pd.DataFrame],
) -> str:
    sections: list[str] = []

    for sheet_name, frame in sheet_frames.items():
        markdown = str(frame.fillna("").to_markdown(index=False) or "").strip()
        sections.append(f"# Sheet: {sheet_name}\n\n{markdown}".rstrip())

    return "\n\n".join(sections)
