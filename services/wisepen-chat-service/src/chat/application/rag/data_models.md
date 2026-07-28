# RAG 导航数据流

这份文档按 `KnowledgeNavigationService` 的三个入口理解整个 RAG：

```text
locate          从用户问题找到私有资料入口
read_sections   沿标题树读取完整正文
expand          沿跨文档知识关系继续跳转
```

三个入口使用同一套状态、权限和证据模型，但解决的是三个不同问题。review 时建议按下面三个平行结构阅读，不要先按目录把所有 dataclass 展开。

## 总览：三条路径如何接起来

```text
knowledge_navigate_locate(query)
    |
    +--> RAG hybrid recall -> SourceRef/ReadingBlock 回源
    +--> Neo4j MENTIONS 找到知识节点
    +--> 创建 state_id 和 known_node_ids
    |
    +--> 返回 Section frontier + graph nodes
              |
              +--> knowledge_navigate_sections(state_id, section_ids)
              |       -> 读取 Section 全部 ReadingBlock 正文
              |       -> 返回 parent / previous / next / children
              |
              +--> knowledge_navigate_expand(state_id, node_ids)
                      -> Neo4j 有界多跳
                      -> 关系 evidence 回源
                      -> 返回新节点及其 Section 来源
```

三个入口共享以下身份链：

```text
resource_id
  -> document_version
  -> content_revision
  -> section_id / reading_block_id / chunk_id / source_ref_id
  -> evidence_ref_id
```

`document_version` 是上游正文版本，`content_revision` 是 RAG 内容投影版本，`relation_revision` 是图关系投影版本。检索和图导航都只接受当前 applied revision，不能把它们混用。

---

## 一、locate：从问题找到阅读入口

### 1. 解决什么问题

`locate` 负责建立一次导航会话的起点：从自然语言问题找到用户可读的私有文档 Section，同时返回这些 Section 中已经解析出的知识节点。

它不是全文阅读，也不是图遍历。它回答的是：

> “和这个问题最相关的资料入口在哪里？入口里有哪些可以继续跳转的节点？”

### 2. 入口参数

服务方法：`KnowledgeNavigationService.locate()`

```text
query             用户的完整问题或概念描述
max_results       最多返回多少个初始来源
session_id        当前聊天会话
permission_scope  user_id + group_role_map
```

Tool 入口是 `knowledge_navigate_locate`，只接收 `query` 和 `max_results`；用户身份、会话和群组角色由 tool context 注入，不由模型传入。

### 3. 内部调用链

```text
query
  -> RagRetrievalRequest
  -> RagKnowledgeLocator.locate()
       -> RagCandidateRetriever.retrieve()
            -> EmbeddingClient
            -> RagCandidateRequest
            -> Qdrant dense + native BM25 candidate
            -> applied content_revision 过滤
            -> ACL 过滤
            -> RankingPipeline
       -> RagRankedHit[]
       -> RagEvidenceMaterializer.materialize()
            -> Mongo SourceRef 回源
            -> Mongo ReadingBlock 回源
            -> 最终 ACL 校验
       -> RagMaterializedHit[]
       -> RagSectionNavigator.build_hits()
            -> RagSectionView
```

#### 召回模型

| 模型 | 在 locate 中的作用 |
|---|---|
| `RagRetrievalRequest` | 服务层接收的完整查询：query、权限、资源范围、top_k、candidate_limit |
| `RagPermissionScope` | 可信请求身份；决定 Qdrant 和后续证据权限 |
| `RagCandidateRequest` | 发送给 Qdrant 仓储的 query text、dense vector、权限和资源范围 |
| `RagRetrievalCandidate` | Qdrant 返回的单个检索 chunk，携带 section、reading block、revision、source ref 和 score signals |
| `RagRankedHit` | 候选经过 RankingPipeline 后的最终排序结果 |

Qdrant candidate 不是最终正文。它的 `raw_text` 可以给排序层使用，但最终证据必须通过 `source_ref_id` 从当前 applied revision 回源。

#### 内容层模型

```text
RagSectionNode
    -> 标题路径、Section 父子关系、own/subtree 范围
RagSectionReadingBlock
    -> Section 内有界正文
RagRetrievalChunk
    -> embedding/BM25 的检索粒度
RagSourceRef
    -> chunk 到 Kafka Markdown 的稳定定位
```

这里没有 Mongo 父块/子块模型：Section 是逻辑结构父节点，ReadingBlock 是 Section 内阅读单元，RetrievalChunk 是检索单元。一个 Section 可以包含多个 ReadingBlock，一个 ReadingBlock 可以包含多个 RetrievalChunk。

### 4. 回源后模型

`RagEvidenceMaterializer` 将 `RagRankedHit` 转换为 `RagMaterializedHit`：

```text
RagMaterializedHit
  ├─ hit: RagRankedHit
  ├─ reading_block: RagSectionReadingBlock
  └─ source: RagMaterializedSource
         ├─ source_ref: RagSourceRef
         └─ content: 从 Markdown parts 按 spans 拼出的权威文本
```

`RagSectionNavigator.build_hits()` 再把它提升为 `RagLocatedSection`，其中的 `RagSectionView` 包含：

- 当前 Section 的标题、路径、摘要和资源版本；
- 当前命中的 ReadingBlock 和 SourceRef；
- parent、previous、next、children 这些轻量 frontier。

locate 不把邻接 Section 正文全部装入结果，正文需要下一步 `read_sections`。

### 5. 图节点和导航状态

命中的 `resource_id + chunk_id` 会组成 `KnowledgeMentionSource`，交给 Neo4j `resolve_mentions()` 查找该 chunk 中已经投影的知识节点。

随后创建：

```text
KnowledgeNavigationState
  state_id
  user_id
  session_id
  root_query
  known_node_ids
```

`known_node_ids` 初始包含：

- locate 找到的图节点 ID；
- 当前 Section、parent、previous、next、children 的 Section ID。

这个集合不是访问日志，而是后续 `read_sections` 和 `expand` 的输入归属校验集合。

### 6. 输出与下一步

`KnowledgeNavigationLocateResult`：

```text
state        导航状态，包含 state_id
nodes        locate 命中的 KnowledgeNavigationNode[]
sources      SectionView[]
```

Tool 返回 `action=locate`、`state_id`、nodes、空的 edges/paths、sources 和 frontier 统计。

下一步有两个方向：

- 想读完整正文：调用 `knowledge_navigate_sections`；
- 想顺着概念、依赖、引用或来源链跳转：调用 `knowledge_navigate_expand`。

---

## 二、read_sections：沿标题树读取正文

### 1. 解决什么问题

`read_sections` 负责把 locate 返回的 Section frontier 变成完整可读正文，并继续暴露下一层标题结构。

它回答的是：

> “这个 Section 的完整内容是什么？从这里还能沿标题树走到哪些相邻节点？”

它不做向量召回，也不做图关系扩展。

### 2. 入口参数

服务方法：`KnowledgeNavigationService.read_sections()`

```text
state_id       locate 或之前 read_sections 返回的导航状态
resource_id    目标 Section 所属资源
section_ids    已在 known_node_ids 中出现的 Section ID
session_id     当前聊天会话
permission_scope
```

Tool 入口是 `knowledge_navigate_sections`。它要求 `state_id + resource_id + section_ids`，并拒绝空 ID、重复 ID 或未知字段。

### 3. 内部调用链

```text
state_id / section_ids
  -> Redis KnowledgeNavigationStateRepository.get()
  -> 校验 state.user_id == 当前 user_id
  -> 校验 state.session_id == 当前 session_id
  -> 校验 section_ids ⊆ state.known_node_ids
  -> RagPermissionAuthorizer.accessible_resource_ids()
  -> RagSectionNavigator.read_sections()
       ├─ load_applied_section_views()
       │    -> SectionNode + parent/sibling/children frontier
       └─ load_applied_section_reading_blocks()
            -> 当前 applied revision 的全部 ReadingBlock
  -> 计算新发现的 frontier Section ID
  -> Redis add_known_nodes()
```

Section 视图和正文块是并行读取的。`RagSectionNavigator` 按请求顺序返回 Section，并按 Section 内 `ordinal` 返回多个 ReadingBlock。

### 4. 核心模型

| 模型 | 在 read_sections 中的作用 |
|---|---|
| `KnowledgeNavigationState` | 证明请求属于当前 user/session，并限制可读取的 Section 范围 |
| `RagSectionView` | 当前 Section 的结构视图，包含 frontier |
| `RagSectionNode` | 标题、路径、摘要、own/subtree offset、父子关系 |
| `RagSectionReadingBlock` | 当前 Section 的完整有界正文块列表 |
| `RagSourceRef` | 不作为主要返回内容，但仍是 ReadingBlock 的权威定位基础 |

这里的“完整正文”指当前 Section 自己的 ReadingBlock 集合，不自动拼接子 Section。子 Section 通过 frontier 暴露，由 Agent 决定是否继续读取。

### 5. 状态变化

读取前只允许提交已经出现在 `known_node_ids` 中的 Section ID。读取成功后，从每个返回 Section 的：

```text
parent / previous / next / children
```

收集新 Section ID，并通过 `add_known_nodes()` 加入 Redis 状态。状态只保存去重后的 ID，不保存正文；正文仍在 Mongo applied revision 中。

如果 state 不存在、用户/会话不匹配、Section 不在 known 集合或 ACL 已变化，请求直接失败，不返回部分正文。

### 6. 输出与下一步

`KnowledgeSectionReadResult`：

```text
state           当前导航状态
sections        带完整 ReadingBlock 的 SectionView[]
new_section_ids 本次 frontier 新发现的 Section ID
```

Tool 返回 `action=read_sections`，每个 Section payload 里的正文通过 `content_index` 指向 ToolReturn 的 cacheable text；同时返回 parent/previous/next/children frontier。

下一步仍然是两种：继续读取新 Section，或者使用其中已经出现的图节点调用 `expand`。

---

## 三、expand：沿跨文档关系继续跳转

### 1. 解决什么问题

`expand` 负责从当前导航状态中已经出现的知识节点出发，沿 Neo4j 的跨文档关系做有界多跳，并把关系证据回源成可读的 Section 来源。

它回答的是：

> “这个概念还依赖、引用、解释或关联了哪些资料中的节点？每条跳转的依据在哪里？”

它不重新做初始语义召回，也不在 Neo4j 中保存标题树正文。

### 2. 入口参数

服务方法：`KnowledgeNavigationService.expand()`

```text
state_id         当前导航状态
node_ids         state.known_node_ids 中已返回的知识节点
relation_types   可选关系类型过滤
direction        in / out / both
max_depth        最大跳数，当前 tool 上限为 2
max_results      最大路径数
session_id       当前聊天会话
permission_scope 当前用户和群组角色
```

Tool 入口是 `knowledge_navigate_expand`。模型只能提交之前结果中的 `state_id` 和 `node_ids`；关系类型和方向是遍历约束，不是任意 Cypher。

### 3. 内部调用链

```text
state_id / node_ids
  -> Redis state 归属校验
  -> node_ids ⊆ state.known_node_ids
  -> KnowledgeGraphExpandRequest
  -> Neo4j KnowledgeGraphNavigationRepository.expand()
       -> ACL predicate
       -> KNOWLEDGE_RELATION / MENTIONS 有界遍历
       -> KnowledgeNavigationPath[]
  -> 删除没有新节点的路径
  -> nodes / edges 按 ID 去重、稳定排序
  -> 收集边的 evidence_source_ref_ids
  -> RagEvidenceMaterializer.materialize_refs()
       -> 按 resource 批量回源 SourceRef
       -> applied revision 校验
       -> 最终 ACL 校验
  -> RagSectionNavigator.build_sources()
       -> SourceRef 提升为 SectionView
  -> Redis add_known_nodes(new_node_ids)
```

### 4. 图模型

```text
KnowledgeNavigationNode
  node_id / kind / label / entity_type / available_relations

KnowledgeNavigationEdge
  edge_id
  source_node_id / target_node_id
  relation_type / relation_profile / predicate
  evidence_resource_id / evidence_ref_ids / source_content_revision

KnowledgeNavigationPath
  nodes[]
  edges[]
  depth = len(edges)
```

这些是导航读取模型，不等于图写入模型：

| 图写入模型 | 导航读取模型 |
|---|---|
| `KnowledgeNode` | `KnowledgeNavigationNode` |
| `KnowledgeMention` | 通过 `KnowledgeMentionSource` 反查节点，不直接暴露为导航节点 |
| `KnowledgeEdge` | `KnowledgeNavigationEdge` |
| `KnowledgeGraphProjection` | Neo4j 中按 revision 应用后的数据 |

`KnowledgeEdge` 的 evidence 会被压缩成 `KnowledgeNavigationEdge` 的引用集合；expand 再通过这些引用回到 Mongo，而不是把关系证据全文塞进 Neo4j 路径结果。

### 5. 路径筛选与状态变化

Neo4j 返回路径后，服务只保留路径中存在新节点的结果：

```text
path.nodes[1:] 中至少一个 node_id 不在 state.known_node_ids
```

之后：

- `nodes` 按 `node_id` 去重并排序；
- `edges` 按 `edge_id` 去重并排序；
- 每条边的 SourceRef 按资源聚合后批量回源；
- 新节点通过 Redis `add_known_nodes()` 加入当前状态。

这使连续 expand 调用具备增量语义：已经展示过的节点不会重复作为新 frontier 返回，但同一状态仍可从已知节点继续指定方向或关系类型扩展。

### 6. 输出与下一步

`KnowledgeNavigationExpandResult`：

```text
state          当前导航状态
nodes          本次路径中的去重节点
edges          本次路径中的去重边
paths          仍然能发现新节点的多跳路径
sources        边证据对应的 SectionView[]
new_node_ids   本次新发现的节点
```

Tool 返回 `action=expand`、nodes、edges、paths、sources 和 frontier 统计。每条 edge 暴露 `evidence_ref_ids`，具体正文在 `sources` 的 cacheable text 中。

下一步可以：

- 对 `new_node_ids` 再次 expand；
- 对 sources 中的新 Section 调用 `read_sections`；
- 继续沿标题树和跨文档关系交替阅读。

---

## 四、三条路径共享的持久化边界

| 后端 | 主要保存/提供 | 三个入口如何使用 |
|---|---|---|
| Mongo | applied revision、Markdown parts、Section、ReadingBlock、SourceRef、ACL projection | locate/expand 回源证据；read_sections 读取 Section 和正文 |
| Qdrant | dense vector、Qdrant native BM25、candidate payload、ACL payload | 只参与 locate 的初始检索 |
| Neo4j | Resource、Entity、ExternalSource、MENTIONS、KnowledgeRelation | locate resolve mentions；expand 有界遍历 |
| Redis | navigation state、context cache、graph extraction cache | 三个入口共享 state；缓存不作为正文或图谱权威源 |

内容层仍然遵循：

```text
SectionNode -> ReadingBlock -> RetrievalChunk -> SourceRef
```

但三条导航路径的重点不同：

```text
locate        RetrievalChunk / SourceRef / MENTIONS
read_sections SectionNode / ReadingBlock / frontier
expand        KnowledgeNavigationPath / Edge evidence / SourceRef
```

仓储协议和持久化实体的后端职责见 [`repositories/README.md`](repositories/README.md)。

## 五、代码阅读顺序

每条路径都按同样顺序读：

1. `knowledge_navigation.py` 中对应的 service 方法；
2. 对应的 application service：`retrieval/locator.py`、`section_navigation/navigator.py`，以及 `repositories/navigation.py` 对应的 Neo4j repository；
3. 对应的模型文件；
4. 对应的 tool renderer，确认最终 Agent 看到的 payload；
5. 最后查看 Mongo/Qdrant/Neo4j/Redis 实现。

推荐实际顺序是：`locate -> read_sections -> expand`。这是 Agent 的自然阅读顺序，也是 RAG 中“先定位、再读正文、最后跨文档跳转”的数据顺序。
