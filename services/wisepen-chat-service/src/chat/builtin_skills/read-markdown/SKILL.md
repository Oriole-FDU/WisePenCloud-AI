---
name: read-markdown
description: Read Markdown files or summarize, outline, and compare whole documents. Convert PDF, DOC, DOCX, PPT, PPTX, XLS, XLSX, or CSV with offline AnyDoc, then read by section or line range.
---

# 通读文档与 Markdown

总结整篇文章、梳理课件结构、对比多份文档时，先拿到 Markdown，再按问题读取章节。已有 `.md` 或 `.markdown` 时直接从读取步骤开始。明确问到原件页码、版式、图表、公式或单元格时，直接加载对应格式 Skill。

## 把文档转为 Markdown

1. 文件已在工作区就用现有路径。会话附件调用 `sandbox_upload_file`，传当前附件的 `attachment_id` 和例如 `input/report.docx` 的 `destination_path`。同名附件使用不同路径。
2. 调用 `sandbox_execute_command`，将下面的 Bash 命令传给 `command`。第一个参数用导入回执的 `path`，第二个参数是新的 Markdown 文件。每份文档分别转换。

```bash
python3 /builtin_scripts/convert_document.py 'input/report.docx' 'reading/report.md'
```

3. 从 stdout 的 JSON 取 `path`（`status` 为 `converted`），下个调用从该文件读正文。PDF 返回 `needs_ocr` 时，调用 `load_skill` 加载 `builtin:pdf`，打开它的选页阅读指南。用 `page_count` 和 `pages` 定位扫描候选页，先取有文字层的相关内容，再按用户问题选择补读哪些页。其他转换错误先看 stderr，再从原格式取所需内容。

## 定位和读连续原文

用 `sandbox_execute_command` 扫描标题或定位关键词。把示例路径和查询词替换成当前任务的值。

```bash
md='reading/report.md'
rg -n -- '^#{1,6}[[:blank:]]+' "$md"
rg -n -F -m 12 -C 3 --max-columns 240 --max-columns-preview -- '适用范围' "$md"
```

只知道主题时，先用 `rg -l -g '*.md' -F -- '适用范围' 'reading'` 找候选文件。目标小节从第 128 行开始，下一同级或上级标题在第 177 行时，读取第 128–176 行。工具 `sandbox_read_file` 的 `start_line` 从零开始，`end_line` 不包含在结果中。

```json
{"file":"reading/report.md","start_line":127,"end_line":176}
```

也可用 Bash 保留原始行号。

```bash
awk 'NR >= 128 { printf "%d:%s\n", NR, $0 } NR == 176 { exit }' 'reading/report.md'
```

- 具体问答读完整相关小节，表格一起读表头和单位，遇到限制条件、引用或脚注时顺着读。没有命中就找同义词或从目录定位。
- 全文摘要按标题顺序覆盖正文，长章切成相邻的行段。工具返回截断时缩短当前行段，从已读位置继续。
- 对比时记录每条发现对应的源文件和章节。需要页号、幻灯片序号或单元格坐标时，回原件定位。
