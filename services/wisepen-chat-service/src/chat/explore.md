# WisePen `knowledge_navigate` 自主调研与实施规划任务

## 一、任务背景

WisePen 当前已经具备静态私有知识库的传统 RAG 能力，主要技术栈包括：

* Elasticsearch：严格词面过滤、元数据过滤、权限前置过滤；
* Qdrant：Dense Vector + BM25 Sparse 主召回；
* Neo4j：知识关系和图增强；
* Redis：会话状态、缓存和临时状态；
* ToolContentStore：长文本缓存、内容引用、分块读取、正则读取和 ranked expand；
* 文档解析：

  * PDF：MinerU；
  * DOCX/PPTX：Docling；
  * HTML、Spreadsheet、JSON、Plaintext 等已有独立 Converter。

传统 RAG 的定位仍然是：

> 针对一次查询，从静态知识库中召回一组可用于回答的文档证据。

现在希望增加一个新的 Agent 阅读能力：

> 让 Agent 像阅读代码仓库一样，沿私有资料中的概念、依赖、引用、定义和来源链进行持续、可解释、按需的导航。

这不是重新实现一套 GraphRAG 问答框架，也不是让图直接生成答案，而是提供一个面向 Agent 的知识导航工具。

拟议的公开工具形态如下：

```python
knowledge_navigate(
    query: str | None = None,
    state_id: str | None = None,
    action: Literal[
        "locate",
        "expand",
    ] = "locate",
    node_ids: list[str] = [],
    relation_types: list[str] = [],
    direction: Literal["in", "out", "both"] = "both",
    max_depth: int = 1,
    max_results: int = 10,
)
```

注意：实际 Python 或 Pydantic 实现中，不应使用可变数组默认值，应使用 `default_factory=list` 或不可变 tuple。

---

# 二、本轮任务目标

本轮不要直接实现代码。

请自主使用 Git 克隆、搜索和阅读指定开源仓库，并结合 WisePen 现有架构，输出一份可执行的实施 Plan。

计划必须回答：

1. `knowledge_navigate` 的准确能力边界；
2. `locate` 与 `expand` 的内部执行流程；
3. 节点、关系、来源引用和导航状态的数据模型；
4. 哪些关系可以确定性构建，哪些关系需要模型抽取；
5. 如何复用现有 ES、Qdrant、Neo4j、Redis 和 ToolContentStore；
6. 如何避免重新实现已有的 RAG 与内容读取能力；
7. 第一阶段 MVP 应实现什么，明确不实现什么；
8. 后续如何逐步增加概念、依赖、引用和来源链能力；
9. 需要修改或新增的 WisePen 模块、文件和接口；
10. 核心风险、评测方法和回滚路径。

计划必须具体到模块、类、数据结构和执行顺序，不能只写概念性架构描述。

---

# 三、核心设计原则

调研和规划过程中必须遵守以下原则。

## 1. 单一公开入口

Agent 侧只暴露一个工具：

```text
knowledge_navigate
```

不要同时暴露：

```text
rag_search
knowledge_navigate
```

原因是第一次查询时，Agent 无法提前判断后续是否需要导航。`locate` 内部直接复用现有混合检索能力即可。

内部可以拆分服务：

```text
KnowledgeNavigateTool
├── KnowledgeLocator
├── KnowledgeTraverser
├── NavigationStateStore
└── NavigationResultBuilder
```

但不要因为内部服务拆分，就向模型暴露多个重叠工具。

## 2. 不重复实现内容读取

`knowledge_navigate` 负责：

* 定位知识节点；
* 返回节点之间的关系；
* 返回来源预览；
* 返回可以读取的 `content_ref`、`chunk_id` 或 source span。

真正的大段原文继续由 WisePen 已有的 ToolContentStore 和内容读取工具负责。

不要在 `knowledge_navigate` 中重新实现：

* 文本窗口扩展；
* regex read；
* ranked expand；
* 长文本分页；
* 内容缓存协议。

第一版不增加 `open` action，除非调研后能证明现有内容读取工具无法承担该职责。

## 3. 原始查询不可被小模型替换

`locate` 必须保留用户或主模型传入的原始 query。

禁止默认执行：

```text
原始 query
→ 小模型 rewrite
→ 只用 rewrite 检索
```

允许将模型产生的辅助信息作为额外信号，例如：

```text
original_query
+ optional_focus
+ extracted_concepts
```

但不得丢弃或覆盖原始 query。

## 4. 图是导航层，不是最终事实层

最终可引用事实必须回到：

* Resource；
* DocumentVersion；
* Section；
* Chunk；
* Span；
* Table；
* Figure；
* 原始引用来源。

图节点、概念节点和推断边只负责导航、组织和候选扩展，不能脱离原文直接作为最终证据。

## 5. 优先使用可靠的原生关系

第一阶段优先构建：

* 文档包含关系；
* 章节父子关系；
* 前后顺序；
* 跨页续接；
* 标题与正文；
* 表格与表头；
* 图片与图注；
* 脚注；
* 显式超链接；
* 文内引用；
* 参考文献指向；
* 明确定义位置。

不要一开始就依赖大规模 LLM 实体关系抽取构建整张图。

## 6. 保持代码紧凑

WisePen 的代码风格倾向：

* 减少琐碎 helper；
* 减少无意义抽象层；
* 避免为了“未来扩展”提前设计复杂框架；
* 保留必要的中文内联注释；
* 避免多重 fallback 和重复执行；
* 每个模块边界明确；
* 优先复用已有服务。

---

# 四、必须调研的开源仓库

只需要调研以下三个仓库。

---

## 1. GitNexus

仓库：

```text
https://github.com/abhigyanpatwari/GitNexus
```

### 调研目的

重点研究它如何让 Agent 从：

```text
query
→ context
→ impact
→ trace
```

逐步阅读代码知识图，而不是只返回代码搜索 Top-K。

特别关注：

* MCP Tool Schema；
* query、context、impact 等工具的职责划分；
* 稳定 UID 如何在后续调用中消除歧义；
* incoming/outgoing relationship 如何分类；
* 关系深度和结果数量如何限制；
* Process、Community 如何被预计算并暴露；
* 搜索结果如何组织为流程，而不是散乱 symbol；
* 工具结果如何控制 token 预算；
* Agent 如何从一次查询结果继续深入。

### 优先阅读

```text
gitnexus/src/mcp/tools.ts
gitnexus/src/mcp/server.ts
gitnexus/src/mcp/resources.ts
gitnexus/src/mcp/local/
gitnexus/src/core/
```

请继续通过搜索定位以下实现：

```text
query handler
context handler
impact traversal
process construction
community construction
hybrid ranking
RRF
symbol UID resolution
result formatting
token budget
```

### 需要输出的结论

明确指出：

1. 哪些设计可以直接映射到 `knowledge_navigate`；
2. 哪些设计只适用于代码图，不适用于通用文档；
3. 是否应该保留 `locate/expand` 两个 action；
4. 是否有必要未来增加 `trace` 或 `path` action；
5. GitNexus 的多工具设计为什么不应直接照搬。

建议映射关系：

```text
GitNexus query
→ knowledge_navigate.locate

GitNexus context
→ knowledge_navigate.expand(max_depth=1)

GitNexus impact
→ knowledge_navigate.expand(
      direction=...,
      max_depth=...
  )

GitNexus source content
→ WisePen ToolContentStore
```

---

## 2. Graphiti

仓库：

```text
https://github.com/getzep/graphiti
```

### 调研目的

重点研究它的：

* Entity；
* Edge / Fact；
* Episode；
* Community；
* 时间和来源关系；
* Node、Edge、Episode 多对象搜索；
* BFS、全文、向量、RRF、MMR 和 Cross-Encoder 组合；
* 原始来源如何与派生关系绑定；
* center node 与 BFS origin 的执行机制；
* 自定义 entity type 和 edge type。

Graphiti 的产品定位是动态 Agent Memory，不要直接照搬整套框架。本次重点是提取适用于静态私有文档的底层设计。

### 优先阅读

```text
graphiti_core/graphiti.py
graphiti_core/nodes.py
graphiti_core/edges.py
graphiti_core/search/search.py
graphiti_core/search/search_config.py
graphiti_core/search/search_utils.py
graphiti_core/search/search_filters.py
mcp_server/src/graphiti_mcp_server.py
```

请继续搜索：

```text
center_node_uuid
bfs_origin_node_uuids
node_bfs_search
edge_bfs_search
node_distance_reranker
maximal_marginal_relevance
rrf
episode source
edge source
group_id
search filters
```

### 需要输出的结论

明确评估：

1. Graphiti 的 Episode 是否适合映射为 WisePen 的 SourceSpan、Chunk 或 DocumentVersion；
2. EntityEdge 是否适合直接保存文档关系；
3. 哪些边应建模为独立 Claim；
4. 如何让每条推断关系绑定原始 source span；
5. Graphiti 的搜索配置模式是否适合 `expand`；
6. 哪些 Graphiti 机制因面向动态记忆而不适合使用。

建议映射：

```text
Graphiti Episode
→ WisePen SourceSpan / Chunk / SourceRecord

Graphiti EntityNode
→ Concept / Entity / Claim

Graphiti EntityEdge
→ Evidence-backed relation

center_node_uuid
→ 当前 focus node

bfs_origin_node_uuids
→ expand.node_ids
```

不要默认接受 Graphiti 的 LLM 实体和关系抽取方案。需要评估如何替换为：

```text
确定性文档关系
+ 有证据的模型抽取关系
+ 查询时临时候选边
```

---

## 3. HippoRAG 2

仓库：

```text
https://github.com/OSU-NLP-Group/HippoRAG
```

### 调研目的

重点研究它如何：

* 从 query 找到 fact / entity 图种子；
* 对 facts rerank；
* 从图种子传播到 passage；
* 使用 Personalized PageRank 或其他图传播；
* 将图结果重新映射回原始文档；
* 图检索失败时回退普通 dense retrieval；
* 记录 graph seeds；
* 执行 IRCoT 式多轮检索。

### 优先阅读

```text
src/hipporag/HippoRAG.py
```

并继续定位：

```text
get_fact_scores
rerank_facts
graph_search_with_fact_entities
personalized PageRank
passage node weight
graph seeds
dense passage fallback
retrieve_ircot
```

### 需要输出的结论

重点回答：

1. HippoRAG 的图传播是否适合 `locate`；
2. 是否适合 `expand` 的 frontier ranking；
3. PPR 是否会过度偏向高连接度 hub；
4. 如何融合 root query、当前 focus、路径深度和 novelty；
5. 哪些部分只适合批量 QA，不适合交互式导航；
6. 是否需要第一阶段实现图传播，还是先使用有界 BFS + rerank。

不要把 HippoRAG 的最终文档排序接口直接当成 `knowledge_navigate` 的返回协议。

---

# 五、需要形成的目标能力模型

## 1. `locate`

职责：

> 根据原始 query，在整个私有知识库中寻找适合作为阅读起点的 canonical knowledge nodes。

内部预计复用：

```text
Elasticsearch strict lexical retrieval
+ Qdrant dense retrieval
+ Qdrant sparse retrieval
+ graph alias / exact name lookup
+ RRF or existing ranking pipeline
```

结果不能只是裸 chunk，必须映射为可继续导航的节点，例如：

```text
Concept
Claim
Definition
Decision
Requirement
Section
Table
Figure
Citation
BibliographicSource
```

`locate` 应返回：

```text
state_id
root_query
nodes
small source previews
content_refs
available relation types
initial paths or structural context
```

需要规划 canonical node resolution：

```text
retrieved chunk
→ concepts / claims / sections / citations
→ deduplicated canonical nodes
```

第一版可以优先返回结构节点和显式概念节点，不必一次完成完整概念消歧。

---

## 2. `expand`

职责：

> 从当前节点沿指定关系有界展开，并结合整个阅读状态返回最值得继续阅读的新节点。

输入：

```text
state_id
node_ids
relation_types
direction
max_depth
max_results
optional query focus
```

`expand` 中的可选 query 只表示本轮局部关注点，不能覆盖 root query。

例如：

```text
root_query:
“权限过滤为什么需要同时存在于 ES 和 Qdrant”

local focus:
“只看与 Kafka 权限同步有关的依赖”
```

排序至少应考虑：

```text
root query relevance
local focus relevance
edge confidence
relation type prior
path coherence
novelty
source authority
visited penalty
depth penalty
hub penalty
```

可以规划类似：

```text
score =
    query_relevance
  + relation_value
  + path_value
  + novelty
  + source_quality
  - redundancy
  - depth_cost
  - hub_penalty
```

但不要为了第一阶段设计过度复杂的学习排序器。

第一阶段应优先：

```text
bounded traversal
+ deterministic scoring
+ optional existing reranker
```

---

# 六、节点模型要求

请在 Plan 中提出明确的数据模型。

至少考虑以下节点类型。

## 原始内容节点

```text
Resource
DocumentVersion
Document
Section
Block
Chunk
Span
Table
Figure
Formula
CitationMention
BibliographicSource
```

## 语义导航节点

```text
Concept
Entity
Claim
Definition
Decision
Requirement
Method
Process
```

不要在第一阶段全部实现。请给出 MVP 节点集合和后续扩展顺序。

---

# 七、Claim 建模要求

不要简单存储：

```text
Concept A -[DEPENDS_ON]-> Concept B
```

通用文档中的依赖、因果和冲突通常具有：

* 条件；
* 时间；
* 来源；
* 作者立场；
* 文档版本；
* 适用范围；
* 不确定性。

优先考虑：

```text
Concept A
    ↑ SUBJECT
Claim X
    ↓ OBJECT
Concept B

Claim X
    ├─ STATED_IN → SourceSpan
    ├─ ASSERTED_BY → Document / Author
    ├─ QUALIFIED_BY → Condition
    ├─ CITED_FROM → BibliographicSource
    └─ EXTRACTED_FROM → Chunk
```

Agent 导航时可以投影成：

```text
A
└─ DEPENDS_ON
   └─ B
```

但必须能继续打开 Claim，看到完整限定条件和原文。

请评估第一阶段是否需要完整 Claim 节点，还是可以先使用带 evidence refs 的 relation record，然后在第二阶段迁移为 Claim。

---

# 八、关系分层要求

## Tier 1：确定性关系

优先从解析结果中构建：

```text
CONTAINS
PARENT_OF
NEXT
PREVIOUS
CONTINUES
SAME_SECTION
CAPTION_OF
FOOTNOTE_OF
TABLE_CONTAINS
HYPERLINKS_TO
CITES
VERSION_OF
```

来源包括：

```text
MinerU content list
Docling document model
HTML DOM
Office XML
Markdown structure
显式引用编号
```

## Tier 2：显式语义关系

只有原文明确表达时才构建：

```text
DEFINES
EXPLAINS
DEPENDS_ON
REQUIRES
IMPLEMENTS
DERIVED_FROM
CONTRASTS_WITH
SUPERSEDES
CONTRADICTS
```

每条关系必须包含：

```text
evidence_span
resource_id
document_version
extractor_version
confidence
qualifiers
```

## Tier 3：候选关系

不要直接写成正式图事实：

```text
SEMANTICALLY_RELATED
SHARED_ENTITY
POSSIBLE_BRIDGE
POSSIBLE_DEPENDENCY
```

此类关系应保留在：

```text
Qdrant
candidate edge store
navigation state
query-time temporary graph
```

---

# 九、状态模型要求

`state_id` 是本工具区别于普通图查询的核心。

请规划类似的数据结构：

```python
@dataclass
class KnowledgeNavigationState:
    state_id: str
    user_id: str
    session_id: str

    root_query: str
    current_focus: str | None

    visited_node_ids: set[str]
    visited_edge_ids: set[str]
    opened_content_refs: set[str]

    frontier: tuple[NavigationCandidate, ...]
    paths: tuple[NavigationPath, ...]

    resource_scope: tuple[str, ...]
    acl_scope_hash: str
    graph_version: str

    node_budget_remaining: int
    content_budget_remaining: int

    created_at: datetime
    expires_at: datetime
```

需要重点考虑：

1. 状态是否只保存在 Redis；
2. 状态 TTL；
3. ACL 变化后如何失效；
4. 图版本变化后如何失效；
5. 是否保存完整 frontier，还是只保存 visited 和 paths；
6. 多次 expand 如何避免返回重复节点；
7. state 是否允许跨 Tool Call 继续使用；
8. 是否绑定 conversation/session；
9. 如何阻止伪造 state_id 越权读取；
10. 如何控制状态体积。

---

# 十、权限要求

WisePen 的知识导航必须完全遵守现有 RAG 权限模型。

任何节点和关系的返回都必须经过：

```text
owner
readable_users
readable groups
excluded users
resource scope
document version
```

权限不能只在 `locate` 时检查一次。

`expand` 沿图遍历时，也必须确保：

* 目标节点绑定的资源对当前用户可读；
* 跨资源边不会泄露不可读节点的名称、数量或存在性；
* state 绑定 ACL scope；
* 权限变化后旧 state 不可继续越权读取；
* Neo4j 图过滤语义与 ES/Qdrant 保持一致。

Plan 中必须明确权限检查发生在哪一层。

---

# 十一、返回协议要求

请规划统一返回模型，至少包含：

```json
{
  "state_id": "kns_xxx",
  "action": "locate",
  "root_query": "...",
  "focus": {
    "query": null,
    "node_ids": []
  },
  "nodes": [],
  "edges": [],
  "paths": [],
  "navigation": {
    "visited_nodes": 0,
    "frontier_nodes": 0,
    "truncated": false,
    "exhausted": false
  }
}
```

每个节点至少考虑：

```json
{
  "node_id": "claim:001",
  "node_type": "claim",
  "label": "...",
  "preview": "...",
  "resource_id": "...",
  "document_version": "...",
  "content_ref": "...",
  "source_span": {
    "start": 0,
    "end": 100
  },
  "available_relations": {
    "depends_on": 2,
    "stated_in": 1
  }
}
```

每条边至少考虑：

```json
{
  "edge_id": "edge_001",
  "source_node_id": "...",
  "target_node_id": "...",
  "relation_type": "depends_on",
  "direction": "out",
  "origin": "explicit_text",
  "confidence": 0.92,
  "evidence_refs": []
}
```

重要原则：

* preview 应尽量来自原文，不默认使用小模型生成摘要；
* 语义边必须提供 evidence refs；
* 返回增量结果，不重复整个历史子图；
* Agent 必须知道每个节点还能沿哪些关系展开；
* 不暴露内部 embedding similarity 为事实关系；
* 不直接返回大量原文；
* content_ref 必须可被现有内容读取工具消费。

---

# 十二、Tool 参数契约

请规划 action-dependent preflight contract。

## `locate`

要求：

```text
query 必填
state_id 禁止
node_ids 为空
```

允许：

```text
max_results
```

通常忽略或禁止：

```text
relation_types
direction
max_depth
```

除非 Plan 能提出清晰语义。

## `expand`

要求：

```text
state_id 必填
node_ids 必填
```

允许：

```text
query
relation_types
direction
max_depth
max_results
```

其中 query 仅为局部 focus。

不要错误地使用现有 `exactly_one_of` 表达全部行为约束。

请评估新增类似：

```text
ToolActionContract
ActionParameterRule
```

是否值得。

要求保持实现简单，不要为一个工具设计过于通用的 DSL。

---

# 十三、MVP 范围

第一阶段建议只实现：

## 节点

```text
Document
Section
Chunk
Table
Figure
Citation
Concept
```

其中 Concept 可以先基于：

* 标题；
* 显式定义；
* glossary；
* 高频术语；
* 已有实体抽取结果。

不要第一阶段就追求完整 ontology。

## 关系

```text
CONTAINS
PARENT_OF
NEXT
PREVIOUS
CONTINUES
CAPTION_OF
CITES
HYPERLINKS_TO
MENTIONS
DEFINED_IN
```

## 动作

```text
locate
expand
```

## 状态

```text
root query
visited nodes
visited edges
paths
ACL scope
graph version
TTL
```

## 返回

```text
canonical nodes
evidence-backed edges
small previews
content refs
available relation counts
paths
```

第一阶段明确不实现：

* 自动全库 Community Summary；
* 全量开放关系抽取；
* 自由 Cypher 暴露给主模型；
* 主模型可见的多个图工具；
* 查询前小模型 rewrite；
* 在线 LLM 自动控制每一步图遍历；
* 长原文直接内嵌；
* 三跳以上一次性展开；
* 自动回答生成；
* 复杂因果图；
* 完整领域 ontology；
* GraphRAG Global Search；
* 分布式图计算；
* 在线训练 selector。

---

# 十四、建议的 WisePen 模块结构

请结合实际仓库继续定位，不要机械照抄。

建议方向：

```text
chat/application/tools/knowledge_navigation/
    knowledge_navigate.py
    models.py
    contracts.py
    navigation_state_store.py

    pipeline/
        locator.py
        traverser.py
        node_resolver.py
        frontier_ranker.py
        result_builder.py

    repositories/
        graph_repository.py
        node_repository.py

    indexing/
        topology_builder.py
        relation_builder.py
```

但要求根据当前 WisePen 目录结构进行调整。

优先复用：

```text
现有 RAG retrieval pipeline
RagPermissionFilterBuilder
ToolContentStore
RankingPipeline
Qdrant client
Elasticsearch client
Neo4j repository
Redis infrastructure
现有 document metadata
```

不要复制已有逻辑到新目录。

---

# 十五、需要输出的 Plan 格式

最终只输出规划，不修改代码。

计划按以下结构组织。

## 1. 仓库调研结论

逐个说明：

```text
GitNexus
Graphiti
HippoRAG 2
```

包括：

* 实际阅读过的关键文件；
* 核心调用链；
* 可迁移设计；
* 不适用设计；
* 对 WisePen 的具体启发。

不要只总结 README。

## 2. WisePen 现状定位

在本地仓库中找出：

* 当前 RAG 入口；
* ES/Qdrant/Neo4j 访问层；
* ToolContentStore；
* Tool 参数 Schema 和 preflight；
* Redis/session store；
* 文档 chunk 元数据；
* 权限过滤；
* graph enhancement；
* ranking pipeline。

列出具体文件路径和关键类。

## 3. 最终架构

包含：

```text
工具入口
内部服务
数据库职责
调用流程
状态流转
权限检查
内容回源
```

最好给出 ASCII 流程图。

## 4. 数据模型

给出建议的：

```text
Node
Edge
SourceRef
NavigationState
NavigationCandidate
NavigationPath
Tool Result
```

字段级设计。

## 5. Tool Contract

分别定义：

```text
locate
expand
```

的参数、校验、执行和返回语义。

## 6. 索引构建方案

说明：

* 哪些数据来自现有解析结果；
* 哪些节点离线创建；
* 哪些关系离线创建；
* 哪些关系查询时创建；
* 如何版本化；
* 如何随文档更新删除旧图；
* 如何和 Kafka 内容事件衔接。

## 7. 在线执行方案

给出：

```text
locate pipeline
expand pipeline
frontier ranking
state update
content_ref materialization
```

的详细顺序。

## 8. 分阶段实施

至少拆为：

```text
Phase 1：可靠文档拓扑 + 基础导航
Phase 2：Concept / Claim / 来源链
Phase 3：依赖、冲突和动态桥接
```

每阶段列出：

* 文件修改；
* 新增类型；
* 测试；
* 风险；
* 验收标准。

## 9. 测试与评测

至少覆盖：

```text
权限隔离
状态失效
节点去重
方向遍历
关系过滤
路径正确性
content_ref 可读性
文档版本更新
高连接度 hub
token budget
最大深度
重复 expand
无结果 fallback
```

评测指标建议包括：

```text
入口定位准确率
关系路径准确率
source grounding rate
重复节点率
无效扩展率
平均调用轮数
平均返回 token
p50 / p95 latency
Agent 完成阅读任务的成功率
```

## 10. 最终实施清单

最后给出按依赖排序的任务列表。

每项包括：

```text
任务
涉及文件
前置依赖
验收标准
风险
```

---

# 十六、规划时需要重点回答的开放问题

1. `locate` 返回 Chunk 节点还是 Concept/Claim 节点为主？
2. Concept 如何生成稳定 ID？
3. 同名概念如何消歧？
4. Section 和 Chunk 是否都需要成为图节点？
5. `MENTIONS` 边是否会造成超级 hub？
6. 如何限制通用实体节点造成的图坍缩？
7. `expand` 默认 `direction` 应为 `both` 还是 `out`？
8. `max_depth` 是否应限制为 1～2？
9. 路径是否保存完整节点序列？
10. 是否需要在 state 中保存 frontier？
11. `state_id` 应绑定哪些权限和版本信息？
12. 图更新后旧 state 如何处理？
13. `content_ref` 指向 Chunk、Span 还是预构造内容窗口？
14. 一个 Claim 对应多个 source span 时如何表达？
15. 如何标识确定性边、显式抽取边和临时候选边？
16. 是否需要对边设置 `origin`、`confidence` 和 `extractor_version`？
17. HippoRAG 的 PPR 是否适合 MVP？
18. GitNexus 的 Process-like structure 在通用文档中应如何定义？
19. 哪些关系值得预计算，哪些关系只应查询时发现？
20. 如何证明 `knowledge_navigate` 比反复调用传统 RAG 更高效？

---

# 十七、执行要求

1. 自主克隆和阅读指定仓库；
2. 自主搜索关键实现，不要只阅读 README；
3. 同时检查 WisePen 当前代码；
4. 不要在信息足够时反复向用户提问；
5. 发现设计风险时必须明确指出；
6. 不要为了迎合初始接口而忽略更好的参数设计；
7. 但除非有明确证据，不要随意增加公开 Tool 数量；
8. 不要直接引入完整第三方框架；
9. 不要修改代码；
10. 最终提交一份足够让后续 Codex 按阶段实施的 Plan。

最终目标不是“接入某个 GraphRAG 库”，而是：

> 在 WisePen 现有静态知识库基础上，建立一套低成本、可回源、有状态、适合 Agent 连续阅读的知识导航能力。
