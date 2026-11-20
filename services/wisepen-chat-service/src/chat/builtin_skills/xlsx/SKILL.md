---
name: xlsx
description: Read, calculate, create, and edit XLSX, XLS, and CSV. Use for exact values, ranges, formulas, charts, and workbook layout. Use read-markdown for a document-wide overview.
---

# Excel 与 CSV

- 了解材料内容或对比多份文档时，加载 `builtin:read-markdown`。
- 查具体数据、公式、表头或单位时，用 `load_skill_asset` 打开本 Skill 的 `references/reading.md`。
- 计算时先定位工作表和范围，按原始数据取数。可用 `sandbox_run_python` 配合 `pandas` 做筛选、汇总和连接，再用表名和坐标说明数据来源。

附件用 `sandbox_upload_file` 导入，后续使用回执的 `path`。旧版 XLS 可用 `xlrd` 原生读取。编辑旧版先用 `sandbox_execute_command` 运行 `soffice --headless --convert-to xlsx --outdir work 'input/report.xls'`，确保 `work` 目录已建好。CSV 用 `csv` 或 `pandas`。

## 编辑工作簿

用 `load_skill_asset` 打开 `examples/workbook.md`，按任务选择区域编辑、新建图表、数据汇总或重算示例。

用 `sandbox_run_python` 传原始多行 Python。下例按已确认的工作表和范围新增合计公式，保存到新文件。

```python
from pathlib import Path
from openpyxl import load_workbook

book = load_workbook("input/report.xlsx")
sheet = book["销售额"]
sheet["B13"] = "=SUM(B2:B12)"
Path("outputs").mkdir(exist_ok=True)
book.save("outputs/report.xlsx")
```

用 openpyxl 更新现有单元格、样式、公式和引用。新建工作簿可用 openpyxl 或 XlsxWriter，图表使用单元格范围作数据源。将有明确输入依赖的计算写成公式，让用户改值时能继续计算。

重新读取修改区域。需要确认公式计算值时，用 `recalculate_workbook.py` 另存重算副本，读取副本中相关公式和保存值，再交付所需版本。用户需要打印版式时按精读指南渲染。用 `sandbox_download_file` 返回成品的下载链接。
