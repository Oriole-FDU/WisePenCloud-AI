# Word 精读

以章节标题、段落和表格定位。DOCX 可用 `python-docx` 读取段落及单元格，按需检查文档元素的先后顺序。旧版 DOC 用 AnyDoc 原生读取，原格式的版式核对走 LibreOffice。

```bash
python /builtin_scripts/render_document.py 'input/report.doc' 'reading/word-pages' --pages 2
```

用回执路径调用 `sandbox_view_image`。引用页号时标注“LibreOffice 渲染第 N 页”，因为字体和排版引擎会影响分页。需要调整导出时可直接使用 `soffice --headless --convert-to pdf:writer_pdf_Export`，指定独立临时 profile 和输出目录。

## 从标题定位段落和表格

先查转换后 Markdown 的标题和关键词。需要精确核对时，从源文件依次读取相关正文、段落与表格，标明段落标题或表题。用 `iter_inner_content()` 保留正文段落与表格交错出现的顺序。页眉页脚、修订、批注及嵌入对象单独检查，按任务说明读取范围。

下面的源码可直接传给 `sandbox_run_python` 的 `source`。

```python
from docx import Document
from docx.text.paragraph import Paragraph

doc = Document("input/report.docx")
for number, item in enumerate(doc.iter_inner_content(), 1):
    if isinstance(item, Paragraph) and item.style.name.startswith("Heading"):
        print(number, item.style.name, item.text)
```

拿到目标位置后缩小读取范围，对齐表头和合并单元格再引用。排版任务实际打开选中页图，检查文字、表格和分页表现。
