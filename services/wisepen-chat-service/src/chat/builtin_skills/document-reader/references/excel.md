# Excel 精读

先确定工作表和所需坐标范围。脚本列出工作表，或返回有限区域内非空单元格及坐标。XLSX 公式与文件保存的结果分开返回，`cached_value: null` 表示没有可读的缓存值。XLS 用 `xlrd` 读取保存值，回执的 `formulas_available: false` 表示没有原始公式表达式。CSV 按原始记录与表头定位。

```bash
python /builtin_scripts/read_workbook.py 'input/report.xlsx'
python /builtin_scripts/read_workbook.py 'input/report.xlsx' --sheet '销售额' --range B2:F20
```

区域过大就按业务范围拆读。统计用 `openpyxl`、`xlrd` 或 `pandas` 对具体工作表与范围计算。需要确认打印布局时，调用共享渲染脚本预览指定的 LibreOffice 打印页。

## 核对数据和计算依据

- 先识别相关工作表、表头所在行、单位、日期格式和小计行。存在多个表区时，每个表区单独定位和取值。
- 精确问题引用工作表和 A1 坐标。公式值问题读公式及保存值。没有保存值时，可根据相关输入独立计算，并在答案中说明计算依据。
- 求和或比较时区分明细与汇总，空单元格与零分开处理。需要确认日期、百分比、隐藏行或合并标题的含义时，再检查源码中的值、格式和结构。

```bash
python /builtin_scripts/read_workbook.py 'input/report.xls' --sheet '销售额' --range A1:D10
python /builtin_scripts/render_document.py 'input/report.xlsx' 'reading/workbook-pages' --pages 1
```

LibreOffice 预览用于查看实际打印布局。工作表、单元格坐标和数值仍从源文件核对。
