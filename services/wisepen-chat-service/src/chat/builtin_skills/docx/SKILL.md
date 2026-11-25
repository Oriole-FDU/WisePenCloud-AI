---
name: docx
description: Read or edit specific Word paragraphs, tables, revisions, headers, and page layouts. Create DOCX files and work from legacy DOC. Use read-markdown for document-wide reading.
---

# Word

- 总结、梳理结构或比对全文，加载 `builtin:read-markdown`。
- 定位段落、表格、页码、修订或版式，用 `load_skill_asset` 打开本 Skill 的 `references/reading.md`。
- 编辑时以原文件或用户提供的模板为起点。旧版 DOC 先用 LibreOffice 转成 DOCX 工作副本。

附件用 `sandbox_upload_file` 导入，后续使用回执的 `path`。转换命令传给 `sandbox_execute_command`。

```bash
mkdir -p work
soffice --headless --convert-to docx --outdir work 'input/report.doc'
```

## 修改现有文档

替换原文、添加批注或生成修订时，用 `load_skill_asset` 打开 `examples/edit.md`，先运行 `docx_document.py inspect` 定位目标。表格、页眉和新文档可直接组合 `python-docx`，打开 `examples/layout.md` 获取可运行示例。

先找标题下的段落、表格单元格或页眉。用 `sandbox_run_python` 的 `source` 传多行代码。下例适用于已定位的文字完整落在一个 run 中的情况。

```python
from pathlib import Path
from docx import Document

doc = Document("input/report.docx")
paragraph = doc.paragraphs[12]
run = next(run for run in paragraph.runs if "草稿日期" in run.text)
run.text = run.text.replace("草稿日期", "发布日期", 1)
Path("outputs").mkdir(exist_ok=True)
doc.save("outputs/report.docx")
```

文字跨多个 run 时，先确认各 run 的文本与样式，再改涉及的 run。段落、表格、节、页眉页脚和图片用 `python-docx`。需要原生批注、修订或字段时，可用 `zipfile` 和 `lxml` 修改相关 OOXML 部件，保持已有关系和样式。新建文档可用 `python-docx` 添加标题、表格和分节，也可用 Pandoc 从草稿生成 DOCX，通过 `--reference-doc` 应用模板样式。

重新打开成品查看目标段落或单元格。涉及排版时渲染修改页，文字流有变化时一起看相邻页。调用 `sandbox_download_file`，传成品的 `path`，使用回执的 `download_url` 交付。
