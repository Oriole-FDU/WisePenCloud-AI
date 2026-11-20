# 用 CLI 编辑 PDF 页面

将需要的命令交给 `sandbox_execute_command`。

```bash
mkdir -p outputs
qpdf --empty --pages 'input/report.pdf' 2-5 -- 'outputs/excerpt.pdf'
qpdf --empty --pages 'input/report.pdf' 1-z 'input/appendix.pdf' 1-z -- 'outputs/combined.pdf'
qpdf 'input/report.pdf' --rotate=+90:3 -- 'outputs/rotated.pdf'
pdfinfo 'outputs/rotated.pdf'
```

需要单页文字、选页 OCR 或表格坐标时，用 `load_skill_asset` 打开 `references/reading.md`。需要从中文或公式源码生成 PDF 时，打开 `references/latex.md`。
