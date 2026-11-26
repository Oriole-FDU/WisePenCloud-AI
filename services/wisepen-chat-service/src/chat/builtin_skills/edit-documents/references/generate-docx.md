# 生成 Word 文档

从 Markdown 草稿生成时，用 Pandoc 套用模板样式；旧版 DOC 先转成 DOCX 副本：

```bash
mkdir -p outputs work
pandoc '<草稿.md>' --reference-doc='<模板.docx>' -o '<输出.docx>'
soffice --headless --convert-to docx --outdir work '<旧版.doc>'
```

从零新建可以用 `python-docx` 加标题、表格和分节，也可以用 Pandoc 加 `--reference-doc` 应用样式。

