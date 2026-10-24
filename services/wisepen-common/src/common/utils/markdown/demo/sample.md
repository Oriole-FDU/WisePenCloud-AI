这是一份演示用的研究笔记。标题前的正文会归入“文档开头”，不会重复展开后面的章节。

# Markdown 结构演示

## 嵌套列表与引用

解析器保留列表和引用的嵌套关系；分块器根据 token 预算选择保留完整结构或按结构边界拆分。

3. 准备研究材料：整理原始文本、表格、公式和插图，保留它们在文档中的出现顺序。
4. 分析文档结构：识别标题层级、正文节点及其来源范围，为后续分块提供结构事实。
   - 核对原文：字符范围使用 Python 字符偏移，中文与 emoji 🙂 不按字节计算。
   - 核对章节：直属正文与章节子树分别记录范围，目录长度依据章节子树计算。
5. 生成检索片段：小段正文可以合并，较长结构优先沿列表项、表格行或代码行拆开。
6. 检查输出质量：查看 token 数、overflow、section_path、node_ids 和 source_spans。
7. 对照结构边界：逐项检查来源节点、章节路径、锚点标签和字符范围。
8. 记录异常情况：保存输入摘要、预算配置、输出序号和需要人工检查的片段。
9. 验证列表分组：观察相邻项何时进入同一 chunk，何时开始新的列表片段。
10. 检查项内结构：长列表项保持完整，项内嵌套内容跟随所属列表项。
11. 检查来源身份：列表被拆分后各片段仍关联原始列表及对应原始项。
12. 复核 token 预算：目标尺寸用于组合决策，阈值用于触发节点拆分。
13. 检查超长节点：不能安全拆分的结构完整保留，并通过 overflow 暴露超限状态。
14. 保留审计信息：分别查看 source_spans、node_ids、section_id 和 content_hash。
15. 比较输出顺序：分块按源文次序排列，chunk_index 从零开始连续编号。
16. 核实标题归属：不同章节之间不会被装入同一个 chunk。
17. 核实嵌套层级：缩进列表仍作为对应列表项的子节点保存。
18. 重跑同一输入：在预算和 tokenizer 固定时比较输出与 hash。

> 引用内容也保留结构。
>
> - 第一条引用内的列表项。
> - 第二条引用内的列表项，包含 **强调** 和 [链接](https://example.com)。

## 表格与图片

Table 1: 示例实验记录

| 阶段 | 输入 | 观察 |
| --- | --- | --- |
| 解析 | 中文段落与标题 | 节点保存结构和字符范围 |
| 解析 | 有序与嵌套列表 | 列表项保留层级关系 |
| 解析 | 引用中的列表 | 引用和列表都保留为节点 |
| 分块 | 连续短段落 | 依据目标预算尝试合并 |
| 分块 | 长有序列表 | 以列表项作为拆分边界 |
| 分块 | 多行 Markdown 表格 | 按行拆分并重复表头 |
| 分块 | 一行很宽的表格 | 保留完整行并标记超限 |
| 分块 | 多行代码块 | 以完整代码行为优先边界 |
| 分块 | 超阈值长列表 | 按列表项分组并保留原节点身份 |
| 分块 | 超阈值 fenced 代码块 | 按完整代码行分段并重建围栏 |
| 分块 | 无法安全拆开的公式 | 保留公式并标记超限 |
| 目录 | 多层 Markdown 标题 | 按父子关系组织章节 |
| 目录 | 带编号的表格 | 锚点归入直属章节 |
| 目录 | 带编号的图片 | 锚点归入直属章节 |
| 目录 | 标题之前的正文 | 投影为文档开头 |
| 范围 | 中文与 emoji 🙂 | 使用字符半开区间 |
| 范围 | 源节点被拆分 | 片段仍引用原始节点身份 |

Table 2: 单行超宽示例

| value |
| --- |
| oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversized-cell oversizedcell oversizedcell oversizedcell oversizedcell oversizedcell oversizedcell oversizedcell oversizedcell oversizedcell oversizedcell |

Figure 1: 文档处理流程示意图

![文档经过解析、分块和目录投影](./pipeline.png)

### 公式原子性

下面的长公式用于观察不可拆单元的 overflow。演示公式只表达一组项的求和。

$$
S = x_{1} + x_{2} + x_{3} + x_{4} + x_{5} + x_{6} + x_{7} + x_{8} + x_{9} + x_{10} + x_{11} + x_{12} + x_{13} + x_{14} + x_{15} + x_{16} + x_{17} + x_{18} + x_{19} + x_{20} + x_{21} + x_{22} + x_{23} + x_{24} + x_{25} + x_{26} + x_{27} + x_{28} + x_{29} + x_{30} + x_{31} + x_{32} + x_{33} + x_{34} + x_{35} + x_{36} + x_{37} + x_{38} + x_{39} + x_{40}
$$

## 代码与长正文

代码片段可以观察围栏在拆分后的输出中如何保留。

```python
def prepare_document(text):
    lines = text.splitlines()
    cleaned = []
    for line in lines:
        if line.strip():
            cleaned.append(line)
    return "\n".join(cleaned)


def describe_chunks(chunks):
    records = []
    for chunk in chunks:
        records.append(
            {
                "index": chunk.chunk_index,
                "tokens": chunk.content_token_count,
                "overflow": chunk.overflow,
                "section": chunk.section_path,
            }
        )
    return records


def display_records(records):
    for record in records:
        print(record["index"], record["tokens"])
        print(record["section"], record["overflow"])
    print("inspection complete")
    print("all source records retained")
    print("section grouping verified")
    print("chunk order verified")
    print("token counts recorded")
    print("anchor labels recorded")
    print("source spans recorded")
    print("overflow flags recorded")
    print("fenced blocks reconstructed")
    print("list items retain structure")
    print("table rows retain structure")
    print("this block is deliberately long")
    print("so the code splitter divides it")
    print("between complete physical lines")
    print("and preserves every code line")
    print("inside a reconstructed fence")
    print("with language metadata retained")
    print("for downstream readers and tools")
    print("all resulting chunks stay visible")
    print("in the generated Markdown report")
```

代码样例包含多行代码，长度超过拆分阈值；结果会按完整代码行分段，并为每段重建完整围栏。长正文用于观察普通段落超过拆分阈值后的处理。分块器依据文本内容和结构进行拆分，目标 token 数是组合时的理想尺寸，拆分阈值决定什么时候需要进一步处理较大的节点。结果中每个 chunk 都有自己的索引、文本、token 数和章节归属。读取这些结果时，需要区分原始节点身份与精确字符覆盖：同一个原始节点可以被拆成多个片段，因此多个 chunk 可以引用相同的 node_id，这并不意味着它们含有相同文本。字符范围只是来源信息，不作为分块尺寸的计量单位。与此同时，表格、公式和图片等结构需要保留自身的语义边界；超宽表格行、无法安全拆分的公式等结构会完整保留，并用 overflow 告诉消费方该片段超过阈值。

# 总结

Parser 输出结构事实，Chunking 组织章节并产生检索片段，Outline 把章节和锚点投影成简洁目录。三个 demo 使用同一份输入，便于对照不同阶段的结果。
