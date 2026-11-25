---
name: pdf
description: Read specific PDF pages, figures, tables, formulas, or scans. Create PDFs, fill forms, merge, split, rotate, and extract pages. Use read-markdown for document-wide reading.
---

# PDF

- 概览、摘要或文档对比加载 `builtin:read-markdown`。
- 指定页、图表、公式、坐标或扫描文字时，用 `load_skill_asset` 打开本 Skill 的 `references/reading.md`。
- 新建含中文、公式、编号或交叉引用的 PDF 时，用 `load_skill_asset` 打开 `references/latex.md`，写 LaTeX 源并用 XeLaTeX 输出。

附件尚未进入工作区时，用 `sandbox_upload_file` 传 `attachment_id` 与 `destination_path`，后续使用回执的 `path`。

需要填写 PDF 时先运行 `pdf_form.py inspect`。用 `load_skill_asset` 打开 `examples/forms.md`，按字段填写或按页图框选填写区域。需要提取、合并或旋转时，打开 `examples/pages.md`。

## 按页编辑

用 `sandbox_execute_command` 运行 qpdf。下例从原件提取第 2–5 页，写到 `outputs/`。

```bash
mkdir -p outputs
qpdf --empty --pages 'input/report.pdf' 2-5 -- 'outputs/excerpt.pdf'
pdfinfo 'outputs/excerpt.pdf'
```

合并、重排或旋转可继续用 qpdf，复杂页序也可用 `pypdf.PdfWriter`。要改现有页面中的一行字或图形，先定位页面和坐标，再用 ReportLab 生成叠加层交给 pypdf 合成。叠加适合补写和盖章，清除底层文本需要真正移除原内容。绘制新页面可直接用 ReportLab。

用 `sandbox_run_python` 的 `source` 传多行 Python 源码。页面有图像时可用 `pdfimages` 提取，用 Pillow 裁图，再用 `sandbox_view_image` 查看。HTML 或 DOCX 需要按排版导出时，可用 Pandoc 或 LibreOffice 处理上游文档。

根据改动读取输出页数和页序，涉及视觉内容时按精读指南渲染修改页。调用 `sandbox_download_file`，传成品 PDF 的 `path`，把回执的 `download_url` 给用户。
