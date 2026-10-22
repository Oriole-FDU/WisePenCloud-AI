# Markdown demos

三个示例共用 [sample.md](sample.md)，结果写入当前目录下的 `results/`，重复运行会覆盖对应结果。

在 `services/wisepen-common` 目录执行：

```powershell
# 运行全部示例
uv run python -m common.utils.markdown.demo

# 也可以分别运行
uv run python -m common.utils.markdown.demo.parser
uv run python -m common.utils.markdown.demo.chunking
uv run python -m common.utils.markdown.demo.outline
```

| 示例 | 结果 | 观察重点 |
| --- | --- | --- |
| Parser | [parser.json](results/parser.json) | 嵌套节点树、kind、text、metadata 和可选 source_spans |
| Chunking | [chunking.md](results/chunking.md)、[chunking.json](results/chunking.json) | 完整 chunk 文本、token 数、overflow、章节归属及锚点 |
| Outline | [outline.json](results/outline.json) | 全局深度限制、完整全局目录、邻域窗口和统一的 Markdown 行格式 |

Chunking 示例采用目标 `80 tokens`、拆分阈值 `160 tokens`。输入中包含超过阈值的长列表和多行 fenced 代码块，用来观察列表项分组及代码围栏重建；单行超宽表格则保留完整行并标记 `overflow`。公式同样保持原子性，超限时保留完整内容并标记 `overflow`。JSON 保留原始字段，可读 Markdown 则逐块列出文本和关键属性。

Parser 的 `source_spans` 是 Python 字符半开区间；没有范围的嵌套节点不会伪造精确位置。Outline 按标题树深度输出 `#`，并在行尾提供 `[C]`、锚点、原始 section ID 和原文起始字符偏移；有外部文档标题时会追加 `<文档开头>` 标明该标题对应的前置正文范围。

样例中的图片路径仅用于演示图片语法和 Figure 锚点，运行不需要读取图片，也不需要联网。默认 tokenizer 使用包内的 o200k_base 数据。
