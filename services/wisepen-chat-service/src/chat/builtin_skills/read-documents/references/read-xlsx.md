# 读取 Excel 工作簿

Excel 用「列字母 + 行号」标识单元格，例如 `B13` 表示 B 列第 13 行；一块连续区域用「左上角:右下角」表示，例如 `A1:F15`。

## 按工作表和区域读取

先列出所有工作表，再指定要读的工作表和区域：

```bash
python3 /builtin_scripts/read_workbook.py '<工作簿>'
python3 /builtin_scripts/read_workbook.py '<工作簿>' --sheet '<工作表>' --range '<左上角:右下角>'
```

读某个区域前，先弄清各列的含义和数值单位；区域较大时按连续行分块读取。

## 公式与缓存值

XLSX 的公式单元格会同时返回 `formula`（公式本身）和 `cached_value`（上次保存时算出的值）；如果保存前没有重算，`cached_value` 可能是过期的。旧版 XLS 只能读到保存值，读不到公式，要看公式先转成 XLSX 再读。

## 引用单元格

回答精确数值时，引用工作表名和单元格地址（例如「销售额!B13」），让用户能在原文件里定位。

## 相关操作

需要重新计算时走 `builtin:edit-documents` 的 `references/recalculate-xlsx.md`；读 CSV 走 `references/analyze-csv.md`；查看打印版式按 `references/view-rendered-pages.md`。
