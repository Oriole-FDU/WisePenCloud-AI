# 替换 Word 文字

先用 `references/locate-docx.md` 拿到目标段落的 `at` 坐标，再替换文字。命令会保留原样式。

## 用脚本替换

```bash
python3 /builtin_scripts/docx_document.py '<输入.docx>' replace '<输出.docx>' \
  --at '<at坐标>' --text '<原文>' --with '<新文>'
```

例如把正文第 12 个对象里的「项目初稿」改成「项目定稿」：

```bash
python3 /builtin_scripts/docx_document.py 'input/report.docx' replace 'outputs/report.docx' \
  --at '12' --text '项目初稿' --with '项目定稿'
```

文字跨多个 run 时，脚本会按原 run 分配样式，额外字符沿用最后一个命中 run 的样式。

## 用 python-docx 替换

涉及超链接、字段、已有修订、绘图或跨段落时，改用 `python-docx` 或 OOXML：

```python
from pathlib import Path
from docx import Document

doc_path = "input/report.docx"    # 换成实际路径
paragraph_index = 12              # 换成目标段落索引
old_text = "草稿日期"             # 换成要匹配的原文
new_text = "发布日期"             # 换成新文字

doc = Document(doc_path)
paragraph = doc.paragraphs[paragraph_index]
run = next(run for run in paragraph.runs if old_text in run.text)
run.text = run.text.replace(old_text, new_text, 1)
Path("outputs").mkdir(exist_ok=True)
doc.save("outputs/report.docx")
```

改完重新打开核对目标段落；文字流有变化时，按 `builtin:read-documents` 的 `references/view-rendered-pages.md` 连相邻页一起看。
