# RAG 能力与数据模型盘点

本文是 RAG 重构的基线盘点。结论只基于当前 `services/wisepen-rag-service` 代码，不把比赛文案或未来 Paper/RAG Tool 设想当成已实现能力。

## 1. 当前能力总览

### 1.1 输入与索引发布

当前服务消费 Kafka 的 `document ready`、ACL 重算和资源物理删除事件。`DocumentReadyPayload` 实际只消费 `resourceId`、`version`、`content`，没有消费 `doc_type`，也没有按文档类型分流。

一份 Markdown 进入 `ResourceIndexer` 后，当前流程是：

```text
document ready
    -> 计算 content_revision
    -> Markdown 结构解析
    -> Section / Page / Anchor / Outline
    -> ReadingBlock（静态父块）
    -> RetrievalChunk（检索子块）
    -> SourceRef（回源引用）
    -> Mongo 暂存
    -> 可选 Contextualize
    -> Dense embedding + Qdrant BM25 sparse vector
    -> ACL 同步与 Qdrant 发布
    -> 资源 revision 激活、旧 revision 清理
    -> 可选图谱抽取与 Neo4j 发布
```

当前采用 staged / applied 两阶段 revision 状态，能够避免新版本未完整写入时污染线上版本，并且通过 `content_revision` 隔离 Mongo、Qdrant 和 Neo4j 的派生数据。

### 1.2 文档结构能力

- 使用 Common 的 Markdown `DocumentChunker` 解析标题、正文、页面标记和锚点。
- 支持 `SECTIONED`、`FLAT_TEXT`、`EMPTY` 三种结构模式。
- Section 保存标题、层级、父节点、文档序号、标题路径、直属正文区间、子树区间和多个正文 span。
- 支持由 Section 生成标题树 Outline，并提供父节点、兄弟节点、孩子节点的局部邻域读取。
- 页面和锚点使用原文字符半开区间，可把阅读结果映射回页码和标题路径。

### 1.3 检索能力

- Qdrant 保存 Dense 向量和 BM25 sparse 向量。
- 查询侧同时接收语义查询和词法查询；未提供词法查询时复用语义查询。
- 当前检索链包含 Weighted RRF、ZeroEntropy rerank、High/Low relevance gate 和 MMR 多样性去重。
- `RetrievalChunk` 是当前最小检索单元，目标 800 字符、100 字符 overlap。
- `RetrievalCandidate` 是 Qdrant 返回的带 revision、Section、span 和 score 的候选，不直接等同于可引用证据。
- LOCATE 会过滤 ACL、精排候选、核验来源，并把命中的块提升为可阅读结果。

### 1.4 阅读与上下文能力

- 当前 `ReadingBlock` 按相邻兄弟 Section 静态装箱，软目标 5000 字符，硬上限 6000 字符。
- 一个 ReadingBlock 可覆盖多个 Section，保留多个原文 span，并在公开展示时补充 Section 标题边界。
- 当前展示逻辑区分“检索命中字段”和“完整阅读文本”，这体现了“检索要精确，阅读要完整”的设计方向。
- 当前 `readPages`、`readSections` 和 `getSurroundingOutline` 支持按页、Section 和标题树邻域继续阅读。
- Contextualize 是可选的索引旁路：只增强 `index_text`，不改写权威 `raw_text`，并通过生成产物缓存复用上下文。

### 1.5 图谱能力

- 当前使用 Neo4j 和 `neo4j-graphrag` 做窗口级实体/关系抽取、确定性校验、规范化合并和发布。
- 图节点目前包含 `Entity`、`Resource`、`ExternalSource`；实体类型和关系类型均采用固定枚举。
- 图事实通过 `GraphEvidence` 绑定到 resource、content revision、ReadingBlock、原文 span 和 quote。
- LOCATE 可以根据文档检索提升的 ReadingBlock 找到图谱 seed 节点。
- EXPAND 支持有界深度遍历、方向控制、关系类型过滤、路径精排、ACL 复查和证据回源。
- 当前有 Redis navigation state 保存已发现节点，并有 Redis 子图候选缓存。

### 1.6 证据、权限和一致性

- `SourceRef` 把检索块绑定到具体发布 revision 和原文坐标。
- `SourceEvidence` 在读取时解析权威 Markdown，验证 revision、Section、ReadingBlock 和文本 span 的一致性。
- 图谱证据使用独立的 `GraphEvidence` / `PublishedGraphEvidence` 链路验证。
- ACL 同步到本地 Mongo、Qdrant payload 和 Neo4j 查询过滤；读取和图谱展开期间还会再次检查权限。
- Kafka 消费失败支持重试和死信消息，资源删除会清理 Mongo、Qdrant 和图谱数据。

## 2. 当前数据模型分类

### 2.1 应保留的稳定核心模型

这些模型表达跨后端、跨流程都需要的事实，不应因更换切分算法或图谱实现而删除。

| 当前模型 | 保留理由 | 重构要求 |
| --- | --- | --- |
| `ContentRevision` | 资源、上游版本、内容哈希和索引规则的稳定命名空间 | 保留；把 `index_schema_version` 拆成明确的 `content_schema_version` / `retrieval_schema_version`，避免所有规则变化共用一个字符串 |
| `DocumentStructure` | 文档模式、总长度、Section、Page、Anchor 的结构事实 | 保留；将结构事实与索引策略解耦 |
| `StructureMode` | 真实存在的三种输入形态 | 保留；`doc_type` 由上游提供时单独消费，不能用它替代结构模式 |
| Common `Section`、`Page`、`Anchor` | 标题树、页码、锚点和原文 span 的权威坐标 | 保留，RAG 只做投影，不复制另一套 Section 语义 |
| `RetrievalChunk` | Dense/BM25 的最小召回单位 | 保留但重命名/收紧字段，成为纯检索索引记录 |
| `RetrievalCandidate` | 检索后尚未核验的候选 | 保留为查询阶段 DTO，不写入权威内容存储 |
| `SourceSpan` | Python 字符半开区间，是回源和证据校验的基础 | 保留，所有模型统一使用同一坐标约定 |
| `SourceRef` | 检索命中到发布 revision 的稳定引用 | 保留但去除对静态 ReadingBlock 的硬依赖 |
| `GraphEvidence` | 图事实回到权威正文的证据合同 | 保留；将 `reading_block_id` 改为可选的动态 `parent_context_id` 或直接用 Section/span |
| `KnowledgeNode`、`KnowledgeMention`、`KnowledgeRelation`、`KnowledgeGraph` | 图谱发布和遍历的核心事实 | 保留为图谱能力的内部领域模型，但关系词汇不要在第一阶段预设 |
| `PermissionScope`、`ResourceAcl`、`GroupResourceAcl` | 检索、阅读、图谱都需要的 ACL 事实 | 保留；领域模型和 Mongo 投影应共用同一命名 |
| `StageAction`、`ResourceIndexStateEntity` | revision staged/applied 生命周期 | 保留；可改名为更通用的 `ResourceRevisionState` |
| `NavigationState` | Agent 多轮图谱探索的会话状态 | 保留，但把它限定为 Agent 导航状态，不承载文档正文或检索结果 |

### 2.2 必须修改的模型

#### A. `ReadingBlock`：从静态索引实体改为动态父块结果

当前 `ReadingBlock` 是在索引阶段预先生成并落 Mongo 的 5000/6000 字符父块。这与目标父块规则不一致：

```text
小文本                         -> 直接取 Section
中文本且 Section 覆盖率高       -> 直接取 Section
长文本且覆盖率不足               -> 围绕命中区间构造窗口
```

因此重构时不应继续把 ReadingBlock 当作固定的内容层级。建议：

- 删除静态 `ReadingBlock` 的领域含义。
- 新增 `ParentContext` 或 `ReadingContext`，在 READ/LOCATE 阶段根据命中 Section、命中 span、覆盖率和预算动态构建。
- 父块结果必须带 `resource_id`、`content_revision`、`section_ids`、`source_spans`、`text`、`page_range`、`section_path` 和构建策略。
- 构建策略使用闭合枚举，例如 `SECTION`、`SECTION_WITH_NEIGHBORHOOD`、`WINDOW`，不要用布尔字段拼装语义。
- 可见性不由父块模型控制；后续由工具缓存接管可见性。

#### B. `RetrievalChunk`：只保留检索所需字段

当前 `RetrievalChunk` 同时携带 `reading_block_id`、页面、锚点和原文 span，导致检索模型依赖静态父块。建议保留：

```text
RetrievalChunk {
  chunk_id
  resource_id
  content_revision
  section_id
  section_path
  raw_text
  index_text
  source_spans
}
```

`page_labels`、`anchor_labels` 应由回源阶段根据 span 投影得到，不作为向量索引的必要事实。`reading_block_id` 删除，改为 `parent_section_id` 或只保留 `section_id`，具体取决于新的 Section/窗口构建器。

#### C. `SourceRef` / `SourceEvidence`：引用坐标与阅读上下文分离

当前 `SourceEvidence` 直接嵌入 `ReadingBlock` 和 `Section`，把“权威证据”和“当次展示上下文”绑在一起。建议分成：

```text
EvidenceRef       = revision + section/chunk + source_spans + quote_hash
EvidenceContent   = EvidenceRef + authoritative_text
ParentContext     = 当次阅读窗口及其 Section 邻域
```

证据验证只依赖 `EvidenceRef` 和权威原文；父块只是展示上下文，不应改变证据身份。

#### D. `DocumentStructure` 与 `ContentRevision`

当前 revision ID 把 resource、document version、内容 hash 和全部索引规则揉成一个字符串。重构建议：

- `ContentRevision` 只描述上游内容版本和内容完整性。
- 单独增加 `DerivedIndexRevision` 或在索引状态中保存 `structure_version`、`retrieval_version`、`graph_version`。
- 图谱、Contextualize 和检索索引可以独立重建，不能因为图谱规则变化而让文档正文 revision 失效。

#### E. 图谱查询模型

`GraphQuerySubgraph` 当前混合了查询参数、缓存元数据和查询结果。建议拆成：

- `GraphQuery`：seed、direction、depth、relation filter、limit。
- `GraphSubgraph`：nodes、edges、mentions、paths。
- `GraphQueryCacheMetadata`：epoch、schema version、TTL 等，仅存在缓存边界。

这样删除 Redis 缓存时，领域模型不需要变化。

#### F. API 结果模型

当前 `CandidateLocateResponse`、`GraphExpandResponse` 直接暴露过多内部视图类型，且 LOCATE 返回 `nodes` 是否存在取决于 graph 开关。建议：

- 对外统一 `RetrievalHit`、`ParentContext`、`EntitySeed`、`EvidenceRef`、`GraphPath` 五类结果。
- `state_id` 只在需要连续图谱探索时返回，不让普通文档检索依赖导航状态。
- page range、标题路径、图谱路径作为展示投影，不回灌领域实体。

## 3. 当前应删除或暂缓的模型

### 3.1 第一阶段删除

| 模型/实体 | 删除原因 | 替代方案 |
| --- | --- | --- |
| `ReadingBlock` | 当前是静态父块，与新的动态父块规则重合且不匹配 | `ParentContext` 动态结果 |
| `ReadingBlockEntity` | 静态父块持久化会锁死切分策略，并增加 revision 清理和回源复杂度 | 仅持久化 Section/原文分片；父块按请求构造 |
| `ReadingBlockSectionView`、`ReadingBlockPresentation` | 只服务于旧 ReadingBlock 展示投影 | `ParentContext` 自带 Section 边界和展示文本 |
| `RetrievalReadingBlockView`、`GraphReadingBlockView` | LOCATE 和 EXPAND 各自复制一套父块响应 | 统一为 `ParentContextView` |
| `GraphSeedBlock` | 名称把图谱 seed 与静态 ReadingBlock 绑定 | 改为 `EntitySeedSource`，引用检索命中 Section/span |
| `SourcePartEntity`（若改为单文档对象存储） | 只是当前 Mongo 分片实现，不是 RAG 语义 | 由 `AuthoritativeContentStore` 内部决定；若仍需分片，保留为 persistence-only，不进 domain |

### 3.2 暂缓，不进入第一阶段核心模型

根据本次重构边界，以下能力可以保留代码作为实验分支，但不应进入新的核心数据合同：

| 模型/能力 | 暂缓理由 |
| --- | --- |
| `KnowledgeRelationType` 固定枚举及 `KnowledgeRelationProfile` | 当前不预设 relation type；关系类型应由后续图谱方案和 Paper schema 决定 |
| `KnowledgeGraphExtractor`、窗口抽取候选 DTO | 本阶段不开启图谱抽取，只先保留图谱查询和能力边界 |
| `GenerationArtifactEntity` 的 `artifact_kind="graph"` | 图谱抽取关闭时没有必要成为第一阶段存储合同；Contextualize 产物可单独保留 |
| `RedisGraphQuerySubgraphCache`、`GraphQuerySubgraph` 的缓存字段 | 本阶段不做图谱缓存；缓存元数据不能污染图谱领域模型 |
| `GraphQueryCacheMetadata`（新增） | 只有实际启用缓存时才需要 |
| `ExternalSource` 节点及 scholarly relation profile | 先作为 TODO，不影响 Generic 文档消费 |
| `doc_type` 专用枚举 | `doc_type` 由上游发送，本服务无法控制；当前只消费 document ready 并按 Generic 处理，不能在 RAG 内部擅自建立强约束 |

## 4. 建议的新模型集合

第一阶段完成后，RAG 核心领域建议收敛为以下模型，不包含实现细节和缓存字段：

### 内容与结构

- `ContentRevision`
- `DocumentStructure`
- `SectionRef` 或直接复用 Common `Section`
- `PageRef`
- `AnchorRef`
- `SourceSpan`

### 检索

- `RetrievalChunk`
- `RetrievalCandidate`
- `RetrievalQuery`
- `RetrievalHit`

### 动态阅读

- `ParentContext`
- `ParentContextStrategy`
- `SectionNeighborhood`
- `ReadingRequest`

### 证据

- `EvidenceRef`
- `EvidenceContent`
- `EvidenceLocation`

### Agent 导航

- `EntitySeed`
- `NavigationState`
- `GraphQuery`（只定义查询能力，不预设关系词表）
- `GraphPath` / `GraphSubgraph`（图谱能力启用后接入）

### 权限与发布

- `PermissionScope`
- `ResourceAcl`
- `ResourceRevisionState`
- `IndexPublishAction`

## 5. 推荐重构顺序

1. 先确定 `ContentRevision`、`DocumentStructure`、`Section`、`SourceSpan` 的坐标合同。
2. 删除静态 ReadingBlock 的领域和 Mongo 语义，建立动态 `ParentContext` 构建器。
3. 收紧 `RetrievalChunk`，让 Qdrant 只依赖检索字段，不依赖父块实体。
4. 重写 `EvidenceRef` / `EvidenceContent`，保证证据验证不依赖展示上下文。
5. 将 LOCATE、READ、邻域阅读统一到 `RetrievalHit -> ParentContext -> EvidenceContent` 链路。
6. 把 ACL 和 revision 发布状态从内容模型中独立出来。
7. 暂停图谱抽取和图谱缓存；保留 `GraphQuery`、`GraphPath` 的能力接口，但不固定 relation type。
8. 后续接入图谱时，再增加实体、关系、路径证据和 Paper 专用 schema 的独立模型。

## 6. 结论

当前最值得保留的是：

```text
权威 Markdown + revision 坐标
    -> Section / 标题树 / Page / Anchor
    -> 小粒度 RetrievalChunk 精确召回
    -> 动态 ParentContext 完整阅读
    -> EvidenceRef 回到原文核验
```

当前最应该移除的是：把 `ReadingBlock` 当成固定、预先落库、同时承担检索归属和阅读展示的中间实体。父块应是一次查询中的动态阅读结果；图谱、关系类型和图谱缓存则在第一阶段保持能力边界，不提前固化数据模型。
