# 定位 Word 内容和页面

按标题、段落或表格名称定位。下面的代码可作为 `sandbox_run_python` 的 `source`，列出正文中使用标题样式的段落及其索引。找到位置后读取相关段落的 `text` 或 `runs`。段落和表格交错时用 `doc.iter_inner_content()` 按文档顺序遍历。

```python
from docx import Document

doc = Document("input/report.docx")
for index, paragraph in enumerate(doc.paragraphs):
    if paragraph.style.name.startswith("Heading"):
        print(index, paragraph.style.name, paragraph.text)
```

表格可从 `doc.tables` 定位，取 `rows` 与 `cells`。阅读批注时用 `doc.comments`，查修订、字段或文本框时用 `zipfile` 和 `lxml` 打开 `word/document.xml` 及对应部件。旧版 DOC 的全文先用 AnyDoc，需检查 Word 对象时转 DOCX 工作副本。

## 看页面

用 `sandbox_execute_command` 运行以下命令。DOC 和 DOCX 都可选页渲染。将回执中的 `images[].path` 传给 `sandbox_view_image`。

```bash
python3 /builtin_scripts/render_document.py 'input/report.docx' 'reading/word-pages' --pages '2-3'
```

页码使用 LibreOffice 渲染后的页序。引用时也可给章节或段落标题，方便用户在自己的 Word 中定位。
