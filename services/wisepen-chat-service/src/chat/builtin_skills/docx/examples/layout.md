# 用模板创建和排版 Word 文档

把下列代码作为 `sandbox_run_python` 的 `source`。示例更新模板中的表格单元格和页眉，并在正文增加一段。列宽同时设置表格、网格列和各单元格，适合固定布局的规则表格。

```python
from pathlib import Path
from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches

doc = Document("input/template.docx")
table = doc.tables[0]
table.cell(1, 0).paragraphs[0].runs[0].text = "业务合计"
doc.sections[0].header.paragraphs[0].runs[0].text = "年度报告"
doc.add_paragraph("汇总范围见上一节。", style="Normal")

table.autofit = False
widths = [Inches(2), Inches(4)]
table_width = table._tbl.tblPr.find(qn("w:tblW"))
table_width.set(qn("w:type"), "dxa")
table_width.set(qn("w:w"), str(sum(width.twips for width in widths)))
for column, width in zip(table.columns, widths):
    column.width = width
for row in table.rows:
    for cell, width in zip(row.cells, widths):
        cell.width = width

Path("outputs").mkdir(exist_ok=True)
doc.save("outputs/report.docx")
```

从文本草稿生成新 Word 文件时，可用 Pandoc 套用模板样式。旧版 DOC 工作副本用 LibreOffice 转换。

```bash
mkdir -p outputs work
pandoc 'work/draft.md' --reference-doc='input/template.docx' -o 'outputs/draft.docx'
soffice --headless --convert-to docx --outdir work 'input/legacy.doc'
```
