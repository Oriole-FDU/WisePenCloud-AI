from pathlib import Path

import pandas as pd
import pytest

from chat.application.utils.document_parse.converters.spreadsheet_converter import (
    SpreadsheetConverter,
)


@pytest.mark.asyncio
async def test_csv_preserves_string_values_and_na_literals(
    tmp_path: Path,
) -> None:
    file_path = tmp_path / "sample.csv"
    file_path.write_text(
        "code,value\n00123,NA\n00001,NULL\n",
        encoding="utf-8",
    )

    result = await SpreadsheetConverter().convert(
        file_path,
        file_name=file_path.name,
    )

    assert "00123" in result
    assert "00001" in result
    assert "NA" in result
    assert "NULL" in result


@pytest.mark.asyncio
async def test_xlsx_preserves_sheets_and_empty_cells(tmp_path: Path) -> None:
    file_path = tmp_path / "sample.xlsx"
    with pd.ExcelWriter(file_path, engine="openpyxl") as writer:
        pd.DataFrame({"code": ["00123"], "value": [""]}).to_excel(
            writer,
            sheet_name="First",
            index=False,
        )
        pd.DataFrame({"name": ["second"]}).to_excel(
            writer,
            sheet_name="Second",
            index=False,
        )

    result = await SpreadsheetConverter().convert(
        file_path,
        file_name=file_path.name,
    )

    assert "# Sheet: First" in result
    assert "# Sheet: Second" in result
    assert "00123" in result
    assert "NaN" not in result
