---
name: read-documents
description: Read and understand documents and archives without modifying them. Convert PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX, or CSV to Markdown; summarize, outline, and compare whole documents; read specific pages, tables, formulas, charts, notes, or cell ranges; OCR scanned pages; view rendered pages; and list or extract ZIP, TAR, TAR.GZ archives. Use when the user wants an answer from a file — reading, summarizing, extracting values, or comparing — not a changed or new file; use edit-documents when the user wants a modified or new file.
---

# 读取文档与压缩包

本 Skill 负责从文档或压缩包里读出内容、给出答案，不会修改任何文件。「读、看、算、总结」都在这里；如果用户要的是「改文件、填表、生成、重算」，请改用 `builtin:edit-documents`。

## 路由

| 用户要 | 打开 |
| --- | --- |
| 查看或解压压缩包 | `references/archives.md` |
| 总结全文、梳理大纲、对比多份文档 | `references/convert-and-read.md` |
| 读 PDF 指定页的文字或表格 | `references/read-pdf.md` |
| 把扫描页转成文字（OCR） | `references/ocr-pdf-pages.md` |
| 读 Word 的段落、表格、批注或修订 | `references/read-docx.md` |
| 读 PowerPoint 的对象、图表或备注 | `references/read-pptx.md` |
| 读 Excel 的工作表、范围、公式或缓存值 | `references/read-xlsx.md` |
| 合并或汇总 CSV | `references/analyze-csv.md` |
| 渲染页面并查看版式（任意格式） | `references/view-rendered-pages.md` |

## 共享约定

- **导入附件**：文件不在工作区时，用 `sandbox_upload_file` 传 `attachment_id` 和 `destination_path`（例如 `input/report.docx`），之后用回执里的 `path`。同名附件用不同的路径区分。
- **只读不改**：本 Skill 只返回内容；任何需要保存成品的操作交给 `builtin:edit-documents`。
- **回原文定位**：需要页码、幻灯片序号或单元格坐标佐证时，回到原件对应位置引用。

