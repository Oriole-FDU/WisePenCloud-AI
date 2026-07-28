# `knowledge_navigate` 实施 Plan

## 目标

让 Agent 像阅读代码仓库一样阅读私有资料：先由现有 RAG 定位原文，再沿跨文档实体、依赖、引用和来源关系跳转；学习资料是主要场景，但底层实体关系同时覆盖产品手册、论文、报告和通用 Markdown 笔记。

```text
RAG 命中 RetrievalChunk
  -> 归并到当前 SectionNode
  -> 读取命中 ReadingBlock 和 SourceRef
  -> 返回 SectionView 与标题树 frontier
  -> 绑定 chunk 中有证据的 KnowledgeEntity
  -> 跨文档有界扩展
  -> 远端 SourceRef
  -> 绑定远端 SectionNode，再构造局部结构上下文
  -> Agent 读取证据并选择下一跳
```

标题树是 Resource 内的结构投影，存 Mongo；Neo4j 只保存跨文档 canonical Entity 和有证据的关系。

MVP 图模型使用 `KnowledgeEntity`、`Resource` 和 `ExternalSource`。`KnowledgeEntity.entity_type` 覆盖 Concept、Person、Organization、Product、Technology、Method、Dataset、Event、Place、Document 和 Other；学习与学术关系通过 profile 扩展。

## 当前缺口

| 能力              | 当前事实                                                           | 需要补齐                                          |
|-----------------|----------------------------------------------------------------|-----------------------------------------------|
| 标题信息            | parser 已保存 `section_path` 和 `heading_level`                    | revision 内 section ID、父子/兄弟顺序和 subtree range  |
| 分块模型            | 已有 `AUTO/BY_PAGE/BY_TITLE`；通用 parent-child 已删除                 | RAG 投影 `SectionNode -> ReadingBlock -> RetrievalChunk` |
| 边界与归一化          | paginated 默认一页一块；flowing 按 title pre-chunk；无事后 merge           | 超长 block 使用统一 Markdown fallback               |
| 原文映射            | `Chunk`、Tool store/reader 已使用 `source_spans` 和 plural locators | RAG projection 分离 `raw_text` 与 `index_text`   |
| section locator | 范围到下一个任意标题                                                     | 保留它作为直接正文范围，另建包含 descendants 的标题树投影           |
| 内容扩展            | `ToolContentWindowBuilder` 按相邻 chunk 序号扩展                      | 根据 Section ID 读取 ReadingBlock，并返回轻量标题树 frontier       |
| 跨文档导航           | 原图增强只在单一 Resource 内找共享实体 chunk                                 | 建立跨 Resource 的通用实体、profile 关系和 `SourceRef`     |
| RAG 迁移          | 完整 RAG 代码仍在 `WisePenCloud-AI-new`                              | 迁移时把 section projection 纳入同一 applied revision |

`formal_pr` 的 chat `document_parse` 不在这条链路中。正文只来自 Java Kafka 的 `resourceId/version/content`，Resource 与
ACL 也只消费 Kafka 权威事实并建立本地查询投影。

## 总体方案

### 1. 改造底层 chunk 系统

Markdown parser 先产出带原文 offset 的结构 blocks，再独立构造 Section、Page 和 Anchor spans。带可信 page marker 的正文默认一页一个
retrieval chunk，只有超长页才在页内按 Section/block 拆分；没有 page 的 Markdown 用 Section/block 优先的 hybrid packer。

每个 ReadingBlock 只属于一个 Section；短 Section 一个块，长 Section 多个块。RetrievalChunk 只属于一个 ReadingBlock，
命中后按 Section 归并为 `SectionView`。`raw_text` 用于证据，带标题路径的 `index_text` 用于
Qdrant。

完整替换范围、装箱算法、page 策略和迁移顺序见 [chunking](./knowledge_navigate_chunking.md)。

### 2. 标题树恢复局部语境

从现有 `TextBlock` 构建版本化 `SectionNode`：记录标题层级、父节点、同级顺序、直接正文范围和子树范围。底层 chunk 通过
source spans 保存结构事实；RAG projector 再把它们投影为只属于一个 Section 的 RetrievalChunk，不把底层 chunk 当成 Section 本体。

`locate` 和远端图跳转都生成 `SectionView`：当前 Section、命中 ReadingBlock、精确 SourceRef，以及只含标题、路径和摘要的
parent/previous/next/children frontier。邻接正文通过 `knowledge_navigate_sections` 按 ID 读取。

具体结构、分块改动和核心代码见 [title tree](./knowledge_navigate_title_tree.md)。

### 3. 通用实体完成跨文档跳转

抽取结果先保留 `EntityMention`，每个 mention 必须指向当前 retrieval leaf 的连续原文 span。canonical Entity 表示跨文档同一人物、组织、产品、技术、方法、数据集、文档或概念，当前语境中的角色说明保留在 mention 或 relation evidence 上。

```text
Chunk A --mention/evidence--> Entity X <--mention/evidence-- Chunk B
Product X --DEPENDS_ON/evidence--> Technology Y
Document A --CITES/evidence--> Document B / ExternalSource
```

共享 Entity 已能产生跨文档候选；core profile 表达组成、使用、依赖、来源和比较，learning/scholarly profiles 增加定义、前置知识、论文引用、方法和数据集关系。抽取 SDK、schema 和 Neo4j 查询见 [graph implementation](./knowledge_navigate_graph_implementation.md)。

### 4. 检索和回读复用现有设施

- Qdrant hybrid + `RankingPipeline` 负责入口召回。
- Mongo 保存 retrieval leaf、SectionNode、source span、mention 和 SourceRef 投影。
- Neo4j 只做跨文档一至两跳关系遍历。
- ACL 从 Kafka 同步到各本地投影，查询热路径不调用 Java。
- `ToolContentStore` 保存可继续读取的原文，导航结果只返回小 preview 和 `cnt_*`。

## 实施阶段

### P0：结构与 chunk 契约

1. 给 heading block 保存 `heading_level`，给 blocks 绑定 page 信息。
2. 从 blocks 独立构建 Section、Page 和 Anchor source spans。
3. 定义 paginated/flowing 分流、字符 soft/hard limit 与 oversized fallback。
4. 保存改造前 length parent/child 样例，作为改革后的回归对照。

### P1：structure-first retrieval chunks

1. paginated 文档正常页直接生成 leaf；flowing 文档实现完整 Section/block 优先的 hybrid packer。
2. 分离 evidence `raw_text` 与检索 `index_text`。
3. 短 Section 只在同父相邻且完整保留时合箱；长 Section 在内部自然边界切分。
4. 删除 parent-child 派生和 heading/短尾 normalization 生产路径。
5. 迁移 locator、`ToolContentStore`、Qdrant payload 到 source spans。

### P2：RAG 命中后的局部结构上下文

1. 将现有 RAG 迁入 `formal_pr` 时增加 section repository 和 applied revision。
2. `RagCandidateRetriever` 命中 retrieval leaf 后批量加载 SectionNode 和邻近结构。
3. `SectionNavigator` 生成 SectionView；正文和 ReadingBlock 写入 `ToolContentStore`。
4. 同 Section 多个 leaf 命中时提升为一个 SectionView；长 Section 返回多个可读取块。
5. `locate` 返回 SectionView；`knowledge_navigate_sections` 负责文档内继续阅读。

### P3：跨文档通用实体

1. 对所有有效 changed retrieval leaf 抽取受控类型的 mention，并绑定精确 evidence span。
2. 用稳定标识、alias exact match、同类型候选召回和证据判定解析 canonical Entity。
3. 建立 `chunk -> Entity <- chunk` 的跨 Resource 候选查询。
4. `expand` 跳到远端 SourceRef 后调用同一个 `SectionNavigator`。

### P4：通用关系与领域 profiles

1. 先启用 core schema 抽取组成、使用、依赖、来源、因果和比较关系。
2. 增加 learning/scholarly profiles，抽取定义、前置知识、引用、作者、方法和数据集关系。
3. 通过 `EvidenceValidator` 后按 Resource revision 写入 Neo4j。
4. 实现 direction、relation type、depth、ACL、visited set 和整项预算；候选先排序再扩下一跳。

## 最小可行实验

准备 30 至 50 篇包含 Markdown 笔记、产品手册和 PDF 转换正文的小语料，人工标注四类任务：

| 任务       | 需要证明的能力                        |
|----------|--------------------------------|
| 同章节事实    | chunk 改造不降低普通 RAG 召回           |
| 前置章节解释   | 标题树能恢复祖先或前一个同级章节               |
| 跨文档同实体   | 同一人物、产品、方法或概念能绑定另一 Resource 的证据 |
| 产品依赖/来源   | core relation hop 后能读到远端证据       |
| 论文引用链     | 能从论文跳到引用文档、方法与数据集             |
| 学习前置关系    | 能从当前概念跳到定义与前置知识                 |

先比较底层 chunk：

```text
A. 改造前 length parent/child + normalization 基线
B. flowing: structure-first leaf
C. B + 同 Section 命中提升
D. paginated: page leaf vs page-bounded leaf
```

再在胜出的 chunk 方案上比较导航能力：

```text
E. + shared canonical Entity
F. + typed relations + bounded expand
```

记录：gold evidence Recall@k、多跳证据链成功率、远端局部语境命中率、无关上下文 token、page citation 准确率、P50/P95 延迟、LLM
索引调用量和单 Section 更新重算范围。ACL 不可读 Resource 的节点、边、计数和来源泄漏必须为 0。

更新实验修改单篇文档的一个 Section，验证只重算受影响 retrieval leaves、mentions、关系证据及必要的相邻上下文。

## 关键验证点

- 短 Section 是否完整保留 path/span，并始终有 retrieval leaf 进入 Qdrant。
- 长 Section 是否优先按完整 block 切分，并可由 source spans 完整重建。
- 正常 PDF 是否一页一个 leaf；异常超长页是否只在页内 fallback。
- 正常 leaf 是否没有通用 overlap；oversized fallback 的 overlap 是否不污染 evidence span。
- `raw_text` 是否精确来自 Kafka spans，`index_text` 的标题增强是否只进入检索索引。
- heading 跳级、重复标题、无标题正文和同名 section 是否能稳定绑定。
- 前置章节补充是否提升答案，同时没有吞入整篇文档。
- canonical Entity 的过度合并和拆分率是否可接受。
- 共享高频实体是否造成候选爆炸；seed ranking、relation filter 和 depth 2 是否足够。
- LLM 抽取失败是否与“没有关系”严格区分，所有返回边能否回到连续原文。
- Section/Chunk/Qdrant 是否只读取同一个 applied content revision；关系边的 source content revision 是否与它一致。

## 文档索引

| 文档                                                                   | 内容                                     |
|----------------------------------------------------------------------|----------------------------------------|
| [chunking](./knowledge_navigate_chunking.md)                         | 底层 chunk 改革、结构优先装箱、长 Section 和 page 策略 |
| [title tree](./knowledge_navigate_title_tree.md)                     | 标题树、SectionView 和局部上下文实现               |
| [architecture](./knowledge_navigate_architecture.md)                 | 模块、运行时、状态、ACL 和数据边界                    |
| [indexing](./knowledge_navigate_indexing.md)                         | Kafka 摄入、版本切换和增量更新                     |
| [relations](./knowledge_navigate_relations.md)                       | 通用实体、关系 profiles、证据校验和多跳阅读             |
| [graph implementation](./knowledge_navigate_graph_implementation.md) | 抽取 SDK、公开 API、Neo4j 写入和有界检索            |
| [tool contract](./knowledge_navigate_tool_contract.md)               | locate/sections/expand 三个工具的参数、返回值和错误语义 |
| [research](./knowledge_navigate_research.md)                         | 源码调研证据与技术取舍                            |
