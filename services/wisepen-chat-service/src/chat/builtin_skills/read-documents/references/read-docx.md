# 读取 Word 内容

## 定位内容

按标题、段落或表格定位。下面列出正文中使用标题样式的段落：

```python
from docx import Document

doc_path = "input/report.docx"  # 换成实际路径
doc = Document(doc_path)
for index, paragraph in enumerate(doc.paragraphs):
    if paragraph.style.name.startswith("Heading"):
        print(index, paragraph.style.name, paragraph.text)
```

找到位置后，读段落的 `text` 或 `runs`。段落和表格交错时用 `doc.iter_inner_content()` 按文档顺序遍历；表格从 `doc.tables` 取 `rows` 和 `cells`。

## 读批注和修订

批注用 `doc.comments` 读；修订、字段、文本框用 `zipfile` 和 `lxml` 打开 `word/document.xml` 及对应部件。旧版 DOC 的全文先用 `references/convert-and-read.md` 转 Markdown，需要查 Word 对象时转成 DOCX 副本。

要看排版时按 `references/view-rendered-pages.md` 渲染。


