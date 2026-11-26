"""读取工作簿指定工作表和范围的单元格，或用 LibreOffice Calc 重算工作簿副本。"""

import argparse
from contextlib import closing
import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory

from openpyxl import load_workbook
from openpyxl.utils.cell import get_column_letter, range_boundaries
import xlrd

CALC_PROFILE = """<?xml version="1.0" encoding="UTF-8"?>
<oor:items xmlns:oor="http://openoffice.org/2001/registry">
  <item oor:path="/org.openoffice.Office.Calc/Formula/Load">
    <prop oor:name="OOXMLRecalcMode" oor:op="fuse"><value>0</value></prop>
  </item>
</oor:items>
"""


def read_workbook(args, parser) -> None:
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


def recalculate_workbook(args, parser) -> None:
    workspace = Path.cwd().resolve()
    source = args.source.resolve()
    source.relative_to(workspace)
    output = args.output.resolve()
    path = output.relative_to(workspace)
    if source.suffix.lower() not in {".xls", ".xlsx"} or output.suffix.lower() != ".xlsx":
        parser.error("use an XLS or XLSX source and a new XLSX output")
    with TemporaryDirectory() as directory:
        stage = Path(directory)
        profile = stage / "profile"
        (profile / "user").mkdir(parents=True)
        # 入站 Excel 可以包含过期的保存值。强制 Calc 在加载 OOXML 时重算。
        (profile / "user" / "registrymodifications.xcu").write_text(CALC_PROFILE, encoding="utf-8")
        command = ["soffice", f"-env:UserInstallation={profile.as_uri()}", "--headless",
                   "--convert-to", "xlsx:Calc MS Excel 2007 XML"]
        if source.suffix.lower() == ".xls":
            # 将旧 BIFF 工作簿先转为 OOXML，统一走强制重算的加载路径。
            subprocess.run([*command, "--outdir", str(stage), str(source)],
                           check=True, timeout=120, stdout=subprocess.DEVNULL)
            source = stage / f"{source.stem}.xlsx"
        calculated = stage / "calculated"
        calculated.mkdir()
        subprocess.run([*command, "--outdir", str(calculated), str(source)],
                       check=True, timeout=120, stdout=subprocess.DEVNULL)
        with (calculated / f"{source.stem}.xlsx").open("rb") as converted:
            output.parent.mkdir(parents=True, exist_ok=True)
            with output.open("xb") as target:
                shutil.copyfileobj(converted, target)
    print(json.dumps({"path": path.as_posix(), "engine": "LibreOffice Calc"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="operation", required=True)

    read = commands.add_parser("read", description="按工作表和有限范围读取原始单元格，并区分公式与缓存值。")
    read.add_argument("source", type=Path)
    read.add_argument("--sheet")
    read.add_argument("--range", dest="cell_range")

    recalculate = commands.add_parser("recalculate", description="用 LibreOffice Calc 计算 XLS 或 XLSX 工作簿的副本。")
    recalculate.add_argument("source", type=Path)
    recalculate.add_argument("output", type=Path)

    args = parser.parse_args()
    if args.operation == "read":
        read_workbook(args, read)
    else:
        recalculate_workbook(args, recalculate)


if __name__ == "__main__":
    main()
