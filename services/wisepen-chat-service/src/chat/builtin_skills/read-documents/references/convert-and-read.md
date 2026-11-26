# 转换文档并通读

本文件用于文档级的整体理解，在需要全文总结、梳理大纲或跨文档对比或按主题定位原文时使用。
输入可以是 PDF、DOC、DOCX、PPT、PPTX、XLS、XLSX、CSV，或已有的 `.md` 文件。
做法是先转成 Markdown 存到工作区，再按问题读取相关章节，避免把全文塞进上下文；要精确定位某页、某个表格、公式或单元格时，改走对应格式的 `references/read-*.md`。

## 把文档转为 Markdown

1. 文件已经在工作区时，直接用现有路径；会话附件则调用 `sandbox_upload_file`，传 `attachment_id` 和 `destination_path`（例如 `input/report.docx`）。同名附件要用不同的路径区分。
2. 用 `sandbox_execute_command` 运行下面的命令。第一个参数是导入回执里的 `path`，第二个参数是新的 `.md` 文件路径；每份文档单独转换一次。

```bash
python3 /builtin_scripts/convert_document.py '<源文件>' '<目标.md>'
```

例如：

```bash
python3 /builtin_scripts/convert_document.py 'input/report.docx' 'reading/report.md'
```

3. 从 stdout 的 JSON 里取 `path`（此时 `status` 为 `converted`）。如果 PDF 返回 `needs_ocr`，先读取有文字层的部分；需要补读的扫描页按 `references/ocr-pdf-pages.md` 处理。其他转换错误先看 stderr 判断原因；确需读原格式的细节时，改走本 Skill 对应格式的 `references/read-*.md`。

## 定位并读取连续原文

用 `sandbox_execute_command` 扫描标题或定位关键词，把示例里的路径和查询词换成当前任务的值。

```bash
md='reading/report.md'
rg -n -- '^#{1,6}[[:blank:]]+' "$md"
rg -n -F -m 12 -C 3 --max-columns 240 --max-columns-preview -- '<关键词>' "$md"
```

只知道主题、不知道文件名时，先用 `rg -l -g '*.md' -F -- '<关键词>' 'reading'` 找出候选文件。

用 `sandbox_read_file` 读取行区间：`start_line` 从 0 起、`end_line` 不含在内。例如目标小节从第 128 行开始、下一个同级或上级标题在第 177 行，就传 `start_line=127, end_line=176`。

```json
{"file":"reading/report.md","start_line":127,"end_line":176}
```

也可以用 Bash 保留原始行号：

```bash
awk 'NR >= <起始行> { printf "%d:%s\n", NR, $0 } NR == <结束行> { exit }' '<文件>'
```

- 回答问题时要读完整小节，表格连表头和单位一起读；遇到限制条件、引用或脚注时，顺着引用追读相关段落。没有命中就换同义词，或顺着标题结构定位。
- 全文摘要按标题顺序覆盖正文，长章切成相邻的行区间；工具返回截断时，缩短区间、从已读位置继续。
- 对比时记录每条发现对应的源文件和章节；需要页码、幻灯片序号或单元格坐标佐证时，回到原件定位。

