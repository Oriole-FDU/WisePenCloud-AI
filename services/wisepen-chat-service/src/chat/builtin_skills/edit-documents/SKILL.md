---
name: edit-documents
description: Modify or create documents in their native format. Replace Word text, add comments, track and accept revisions, lay out Word from a template or generate from Markdown, fill PDF forms (AcroForm fields or static forms by coordinates), overlay text or stamps, rearrange, merge, and rotate PDF pages, generate PDF from LaTeX, edit PowerPoint charts, tables, and notes, edit Excel cells and formulas, create chart workbooks, and recalculate workbooks. Use when the user wants a changed or new file delivered; use read-documents for read-only answers.
---

# 编辑与生成文档

本 Skill 负责修改或生成文档，产出一个成品文件。「改、填、生成、重算」都在这里；如果用户只是要一个答案（读、看、算、总结），请改用 `builtin:read-documents`。

## 路由

| 用户要 | 打开 |
| --- | --- |
| 定位 Word 段落或表格（取 at 坐标） | `references/locate-docx.md` |
| 替换 Word 文字并保留样式 | `references/replace-docx-text.md` |
| 给 Word 加批注 | `references/comment-docx.md` |
| 生成或接受 Word 修订 | `references/track-docx-changes.md` |
| 从 Markdown 或模板生成 Word | `references/generate-docx.md` |
| 填 PDF 的 AcroForm 字段 | `references/fill-pdf-form.md` |
| 在静态 PDF 表单上按坐标填字或盖章 | `references/overlay-pdf.md` |
| 抽取、合并或旋转 PDF 页面 | `references/rearrange-pdf-pages.md` |
| 从 LaTeX 生成 PDF | `references/generate-pdf-latex.md` |
| 修改 PowerPoint 的图表、表格、备注或文字 | `references/edit-pptx.md` |
| 修改 Excel 的单元格或公式 | `references/edit-xlsx.md` |
| 新建带图表的 Excel 工作簿 | `references/create-xlsx-chart.md` |
| 强制重算 Excel 工作簿 | `references/recalculate-xlsx.md` |

## 共享约定

- **导入附件**：与 `read-documents` 相同，用 `sandbox_upload_file` 导入后取回执里的 `path`。
- **改前先定位**：先按 `builtin:read-documents` 的 read 指南读当前值和结构，再修改，改完重新打开成品核对。
- **交付**：成品生成后，先问用户要下载文件还是保存到云盘，不要默认下载。用户要下载时，用 `sandbox_download_file` 传成品 `path`，把回执里的 `download_url` 给用户；用户要保存到云盘时，用 `sandbox_upload_file_to_cloud_drive` 把成品上传为云盘资源。
- **核对排版**：改完要检查版式时，按 `builtin:read-documents` 的 `references/view-rendered-pages.md` 渲染并查看。

