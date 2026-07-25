# 标题树与局部上下文

## 作用

标题树不承担跨文档检索，也不进入 Neo4j。它把一次 RAG 命中或图跳转落到 Resource 内的准确 section，并返回类似“文件路径 +
enclosing symbols + 相邻定义”的局部语境。

```text
跨文档 SourceRef
  -> section_id
  -> ancestors + current + previous sibling + children
  -> 原始 chunk/span/content_ref
```

## 当前实现需要修正的位置

| 文件                                 | 当前行为                    | 修改                                                    |
|------------------------------------|-------------------------|-------------------------------------------------------|
| `chunkers/markdown/parser.py`      | heading level 只存在解析过程   | 写入 heading block 的 `metadata["heading_level"]`        |
| `chunkers/markdown/chunker.py`     | page/size 混合决定 chunk    | paginated 一页一 leaf；flowing 按 Section/block 装箱；超长输入才细分 |
| `chunkers/_utils/normalization.py` | heading/短尾在切分后重新改边界     | 移出生产路径，合箱规则在 packer 中一次完成                             |
| `chunkers/parent_child/chunker.py` | parent/child 都是长度层级     | 由真实 Section/Page 到 retrieval leaf 的映射替代               |
| `chunkers/markdown/locator.py`     | locator 依赖最终 chunk 覆盖关系 | 先建立 source range，再映射到零至多个 retrieval leaves            |

## SectionNode

只保存运行时消费者会读取的字段：

```python
@dataclass(frozen=True, slots=True)
class SectionNode:
    section_id: str
    resource_id: str
    document_version: str
    title: str
    level: int
    parent_section_id: str | None
    ordinal: int
    section_path: tuple[str, ...]
    own_start: int
    own_end: int
    subtree_end: int
```

- `own_start..own_end`：标题到下一个任意标题，表示本节点的直接正文。
- `own_start..subtree_end`：标题到下一个 level 小于等于当前节点的标题，包含 descendants。
- children 用 `(parent_section_id, ordinal)` 查询，previous sibling 用同一 parent 下的前一 ordinal 查询，不重复存 ID 数组。
- `section_id` 是 revision 内标识；`resource_id/document_version/heading_start` 生成即可。跨版本稳定性由 applied revision
  和 SourceRef 解决，不把 offset ID 当 canonical 知识 ID。

无标题正文建立一个 `document_root` 节点；标题前的 preface 作为 root 的直接正文。重复标题依赖 section ID 和位置区分，不以标题文本作主键。

## 构树算法

输入直接使用 `MarkdownParser.parse()` 的 blocks，不重新正则解析 Markdown：

```python
def build_section_tree(blocks, *, resource_id, document_version, text_length):
    headings = [block for block in blocks if block.block_kind == BlockKind.HEADING]
    stack = []
    nodes = []

    for index, heading in enumerate(headings):
        level = int(heading.metadata["heading_level"])
        while stack and stack[-1].level >= level:
            stack.pop()

        parent = stack[-1] if stack else None
        own_end = headings[index + 1].start_offset if index + 1 < len(headings) else text_length
        node = SectionNode(
            section_id=section_id(resource_id, document_version, heading.start_offset),
            resource_id=resource_id,
            document_version=document_version,
            title=str(heading.metadata["title"]),
            level=level,
            parent_section_id=parent.section_id if parent else None,
            ordinal=next_ordinal(parent),
            section_path=heading.section_path,
            own_start=heading.start_offset,
            own_end=own_end,
            subtree_end=text_length,
        )
        nodes.append(node)
        stack.append(node)

    # 反向扫描：第一个 level <= 当前 level 的后续 heading 决定 subtree_end。
    return finalize_subtree_ranges(nodes, text_length)
```

实际实现中 offsets 必须是非空整数并验证单调；`subtree_end` 用反向单调栈计算。示例省略错误 DTO。构树是 O(number of headings)
，不调用 LLM。

## 弱结构文档

先计算结构质量：有效 heading 数、层级变化、最长无 heading 文本和标题覆盖率。正常 Markdown 走确定性构树；只有正文很长且标题结构明显缺失时，才进入可选
structure repair：

```text
候选行 + page markers + bounded text windows
  -> LLM: title, level, evidence_quote
  -> exact quote 定位
  -> offset 单调、范围和相邻已验证节点校验
  -> SectionNode
```

LLM 只提出结构锚点，不生成正文或相信模型 offset。无效 candidate 丢弃；不足以形成可靠层级时保留 `document_root`，仍可使用普通
RAG。该路径作为 P0 之后的独立实验，不阻塞标准 Markdown MVP。

## 与 retrieval chunk 的关系

SectionTree 在 chunking 前由 source-backed blocks 建立，Section 与 retrieval leaf 是多对多关系：

- paginated 文档通常一页一个 leaf，一页可以覆盖多个 Section；
- 跨页 Section 关联多个 page leaves；
- flowing 文档中的短 Section 可以完整合箱；
- 超长 Section 可以拆成多个结构化 leaves。

每条关联保存 Section 与 leaf 的相交 source span。`SectionView` 读取 Section 自身的 `own_start..own_end`，不拼接
`Chunk.text`，因此 chunk 策略变化不会改变 Section 语义。

具体分流、装箱和 oversized fallback 见 [chunking](./knowledge_navigate_chunking.md)。

## SectionContext

`SectionContextBuilder` 输入命中 retrieval leaf、source spans 和 SectionNodes，输出：

```text
section_id, section_path,
current_source,
ancestor_preambles,
previous_sibling,
children,
truncated
```

预算顺序：

1. 命中 leaf 和证据 span；
2. 当前 Section 可用范围；
3. ancestor 标题及各自 `own` 范围内、首个 child 之前的短导语；
4. 前一个同级 section 的标题和尾部短片段；
5. 直接 child 标题；与 query 匹配时再带 child preview。

builder 按 `section_id` 批量加载当前、祖先、前置同级和子章节 source ranges。完整 Section 放得下时直接物化；过长时保留命中
leaf、同 Section 相邻 leaves 和继续读取入口。所有片段先按 SourceRef 整项构造，再按预算丢弃低优先级 item；完整内容写入
`ToolContentStore`。

`locate` 和图 `expand` 共用该 builder：跨文档层只负责给出远端 SourceRef，局部结构恢复不区分入口来自 RAG 还是 Neo4j。

## RAG 投影改动

完整 RAG 迁入 `formal_pr` 时增加：

```text
application/rag/ingestion/section_tree.py
application/rag/section_context/builder.py
application/rag/section_context/repository_protocol.py
core/persistence/mongo/rag_section_repository.py
domain/entities/rag_section.py
```

`RetrievalChunk` 和 `SourceRef` 保存 source spans，并由投影表关联 Section/Page。SectionNodes 与 leaves 在同一 staged
projection 写入并一同切换 applied；标题树不是独立的最终事实源。

## 测试

- 有 page marker 的正常正文一页一个 retrieval leaf，一页多个 Section 的 spans 都保留。
- 跨页 Section 能从多个 page leaves 还原完整 `SectionView`。
- 无 page 的短 Section 可合箱但 locator 和 source range 仍独立。
- 超长 Section 在自然结构边界切分，所有 leaf spans 并集覆盖原 Section。
- 每个 leaf 的 `raw_text` 可由 Kafka source spans 精确重建。
- H1 -> H3 -> H2 的 parent、ordinal、own range 和 subtree range 正确。
- 重复标题和无标题 preface 可用 section ID 区分。
- 命中子章节时返回祖先导语、前一个同级和直接 children，且每项可回读原文。
- 远端 SourceRef 与本地 RAG hit 构造的 SectionContext 形状一致。
