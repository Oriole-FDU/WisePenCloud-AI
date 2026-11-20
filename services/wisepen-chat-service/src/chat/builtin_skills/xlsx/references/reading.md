# 按工作表和坐标读取

用 `sandbox_execute_command` 调用 `read_workbook.py`。先列工作表，再读包含表头和数据的范围。XLS 和 XLSX 使用同一入口。

```bash
python3 /builtin_scripts/read_workbook.py 'input/report.xlsx'
python3 /builtin_scripts/read_workbook.py 'input/report.xlsx' --sheet '销售额' --range 'A1:F15'
```

定位时同时看表头、单位、日期及小计行。大范围按表区或行段拆读。XLSX 公式单元格查看 `formula` 和 `cached_value`。用户需要重新计算时，用 `load_skill_asset` 打开 `examples/workbook.md`，运行 `recalculate_workbook.py`，再读取副本中的同一范围。旧 XLS 原件可读取其保存值，查看公式可转成 XLSX 后定位。给出精确结论时引用工作表与 A1 坐标。

CSV 用标准库 `csv.reader` 读取，先确定表头和分隔符。计算可用 pandas，按需要显式选择 `sheet_name`、`usecols` 或 CSV 的 `sep`。求和前区分明细与小计，避免重复统计。

## 查看打印布局

在 `sandbox_execute_command` 运行渲染命令，再用 `sandbox_view_image` 打开 `images[].path`。

```bash
python3 /builtin_scripts/render_document.py 'input/report.xlsx' 'reading/workbook-pages' --pages '1-2'
```

打印页按分页顺序编号。查某个值时回到对应的工作表和单元格。
