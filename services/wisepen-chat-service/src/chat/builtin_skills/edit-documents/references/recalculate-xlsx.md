# 重算 Excel 工作簿

原文件的保存值可能是过期的（保存前没重算）。要得到最新结果时，用 LibreOffice Calc 强制重算并另存副本，原件不动：

```bash
python3 /builtin_scripts/recalculate_workbook.py '<输入.xlsx>' '<重算输出.xlsx>'
python3 /builtin_scripts/read_workbook.py '<重算输出.xlsx>' --sheet '<工作表>' --range '<A1范围>'
```
重算用的是 Calc 自己的计算引擎，某些 Excel 专有函数或外部链接可能不被支持，重算结果可能与 Excel 不同。

用户要重算结果时，交付重算副本；如果用户问的只是原文件的保存值，则读原件同一范围回答。
