# 编辑 Excel 单元格与公式

先按 `builtin:read-documents` 的 `references/read-xlsx.md` 定位工作表和范围、读当前值，再用 openpyxl 更新：

```python
from pathlib import Path
from openpyxl import load_workbook

workbook_path = "input/report.xlsx"  # 换成实际路径
sheet_name = "销售额"                 # 换成实际工作表名
cell_ref = "B2"                      # 换成目标单元格
cell_value = 120                     # 换成目标值
formula_cell = "B13"                 # 换成放公式的单元格
formula = "=SUM(B2:B12)"             # 换成公式或值

book = load_workbook(workbook_path)
sheet = book[sheet_name]
sheet[cell_ref] = cell_value
sheet[formula_cell] = formula
Path("outputs").mkdir(exist_ok=True)
book.save("outputs/report.xlsx")
```

可以更新单元格、样式、公式和引用；旧版 XLS 先用 `soffice --headless --convert-to xlsx` 转副本。
某个格子的值由其他格子算出来时，写成公式而不是写死结果，这样用户改输入格后结果还能自动更新。
改完重读修改区域；要确认公式的计算值走 `references/recalculate-xlsx.md`。
