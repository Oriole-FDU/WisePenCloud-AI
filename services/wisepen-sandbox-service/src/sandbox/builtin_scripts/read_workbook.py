"""按工作表和有限范围读取原始单元格，并区分公式与缓存值。"""

import argparse
from contextlib import closing
import json
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, range_boundaries
import xlrd


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("--sheet")
    parser.add_argument("--range", dest="cell_range")
    args = parser.parse_args()
    source = args.source.resolve()
    source.relative_to(Path.cwd().resolve())
    if (args.sheet is None) != (args.cell_range is None):
        parser.error("--sheet and --range must be supplied together")
    if args.cell_range:
        left, top, right, bottom = range_boundaries(args.cell_range)
        if (None in (left, top, right, bottom) or not
                (1 <= left <= right <= 16384 and 1 <= top <= bottom <= 1048576) or
                (right - left + 1) * (bottom - top + 1) > 500):
            parser.error("use a bounded A1 range of at most 500 cells")

    if source.suffix.lower() == ".xls":
        # xlrd 返回旧版工作簿中保存的值，无法提供公式表达式。
        with xlrd.open_workbook(source, on_demand=True) as workbook:
            if args.sheet is None:
                result = {"sheets": workbook.sheet_names()}
            else:
                sheet = workbook.sheet_by_name(args.sheet)
                cells = {
                    f"{get_column_letter(col)}{row}": sheet.cell_value(row - 1, col - 1)
                    for row in range(top, min(bottom, sheet.nrows) + 1)
                    for col in range(left, min(right, sheet.row_len(row - 1)) + 1)
                    if sheet.cell_type(row - 1, col - 1) != xlrd.XL_CELL_EMPTY
                }
                result = {"sheet": args.sheet, "range": args.cell_range, "cells": cells, "formulas_available": False}
    else:
        with closing(load_workbook(source, read_only=True, keep_links=False)) as workbook:
            if args.sheet is None:
                result = {"sheets": workbook.sheetnames}
            else:
                bounds = dict(min_col=left, min_row=top, max_col=right, max_row=bottom)
                with closing(load_workbook(source, read_only=True, data_only=True, keep_links=False)) as values:
                    cells = {}
                    for row, cached_row in zip(
                        workbook[args.sheet].iter_rows(**bounds), values[args.sheet].iter_rows(**bounds), strict=True,
                    ):
                        for cell, cached in zip(row, cached_row, strict=True):
                            if cell.data_type == "f":
                                cells[cell.coordinate] = {"formula": cell.value, "cached_value": cached.value}
                            elif cell.value is not None:
                                cells[cell.coordinate] = cell.value
                result = {"sheet": args.sheet, "range": args.cell_range, "cells": cells}
    print(json.dumps(result, ensure_ascii=False, default=str))


if __name__ == "__main__":
    main()
