# 新建带图表的 Excel 工作簿

用 XlsxWriter 新建，图表以单元格范围作数据源：

```python
from pathlib import Path
from xlsxwriter import Workbook

sheet_name = "销售额"                # 换成工作表名
headers = ["月份", "收入"]           # 换成表头
categories = ["一月", "二月", "三月"]  # 换成分类
values = [120, 135, 128]            # 换成数据
series_name = "收入"                # 换成系列名

Path("outputs").mkdir(exist_ok=True)
with Workbook("outputs/chart.xlsx") as book:
    sheet = book.add_worksheet(sheet_name)
    sheet.write_row("A1", headers)
    sheet.write_column("A2", categories)
    sheet.write_column("B2", values)
    chart = book.add_chart({"type": "column"})
    chart.add_series({"name": series_name, "categories": [sheet_name, 1, 0, 3, 0],
                      "values": [sheet_name, 1, 1, 3, 1]})
    sheet.insert_chart("D2", chart)
```

生成后按 `builtin:read-documents` 的 `references/read-xlsx.md` 重读，核对数据区。

