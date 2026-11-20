# 编辑、计算和新建工作簿

用 `sandbox_run_python` 更新现有模板中的目标格。保留其他单元格和原有格式。

```python
from pathlib import Path
from openpyxl import load_workbook

book = load_workbook("input/report.xlsx")
sheet = book["销售额"]
sheet["B2"] = 120
sheet["B13"] = "=SUM(B2:B12)"
Path("outputs").mkdir(exist_ok=True)
book.save("outputs/report.xlsx")
```

按需生成一份重算副本。用 `sandbox_execute_command` 运行以下命令，再从 `cells.B13` 读取公式和已保存的计算值。当用户问原文件中的保存值时，先读取原件中的同一范围。Calc 对 Excel 专有函数、外部链接等使用自己的支持能力。

```bash
python3 /builtin_scripts/recalculate_workbook.py 'outputs/report.xlsx' 'outputs/report-calculated.xlsx'
python3 /builtin_scripts/read_workbook.py 'outputs/report-calculated.xlsx' --sheet '销售额' --range 'B13'
```

## 新建带可编辑图表的 Excel 文件

```python
from pathlib import Path
from xlsxwriter import Workbook

Path("outputs").mkdir(exist_ok=True)
with Workbook("outputs/chart.xlsx") as book:
    sheet = book.add_worksheet("销售额")
    sheet.write_row("A1", ["月份", "收入"])
    sheet.write_column("A2", ["一月", "二月", "三月"])
    sheet.write_column("B2", [120, 135, 128])
    chart = book.add_chart({"type": "column"})
    chart.add_series({"name": "收入", "categories": ["销售额", 1, 0, 3, 0],
                      "values": ["销售额", 1, 1, 3, 1]})
    sheet.insert_chart("D2", chart)
```

## 读 CSV、合并与汇总

```python
import pandas as pd

sales = pd.read_csv("input/sales.csv", encoding="utf-8-sig", dtype={"门店编号": str})
stores = pd.read_excel("input/stores.xlsx", sheet_name="门店", dtype={"门店编号": str})
joined = sales.merge(stores[["门店编号", "地区"]], on="门店编号", how="left", validate="many_to_one")
print(joined.groupby("地区", dropna=False)["收入"].sum().to_string())
```
