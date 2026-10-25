# RAG V4 重构实施计划

本文件保留总体目标、问题范围和不做事项。具体实现顺序以 [10-实施排期与验收门槛.md](10-%E5%AE%9E%E6%96%BD%E6%8E%92%E6%9C%9F%E4%B8%8E%E9%AA%8C%E6%94%B6%E9%97%A8%E6%A7%9B.md)
为准，最终组件合同以 [11-RAG-V4设计文稿.md](11-RAG-V4%E8%AE%BE%E8%AE%A1%E6%96%87%E7%A8%BF.md)
为准。本文件中的历史迁移叙述不覆盖这两份收敛后的实现合同。

## 0. 决策结论

这次工作按“收敛后重做 RAG 核心编排”执行，不在现有 `domain/application/container` 结构上继续叠加补丁。原因是当前半成品同时承担了四种不同职责：Markdown
结构投影、逐 chunk 垂类 metadata、普通检索编排和图谱事实生命周期；这些职责已经互相污染，`ContextMigrationRequired`
也说明当前上下文链路没有完成。

本计划固定以下边界：

- 文档和内容事件继续只消费现有 `resource_id`、`version`、Markdown `content`；ACL 相关 RAG V4
  工作按 [03-ACL 基础投影与一致性边界.md](03-ACL%20基础投影与一致性边界.md) 直接消费现有 ACL 事件，不把未来上游完成事件作为开工门槛。
- `utils/markdown` 负责 Markdown 解析和分块，`utils/retrieval` 负责候选、Fusion 和 ranking 组合；RAG 不复制它们的基础能力。
- `Chunk` 是 RAG 的基本单元。不会引入 LlamaIndex `Node`、`NodeWithScore`、`BaseNodePostprocessor` 或第二套通用检索接口。
- 写入侧统一采用 `Chunker`、`Transformer`、`Indexer`、`Storage`、`Retriever` 五类核心抽象，由 `IngestionPipeline` 负责按顺序组合；
  `Document` 只进入 `Chunker.split()`，后续阶段只接收 Chunk 序列。
- Pipeline 配置由上层根据 Document 策略选择并复用；Transformer 接收完整列表，可原地加工 metadata，Indexer
  只读。各阶段按配置顺序串行执行、fail-fast，无回滚；部分索引可见性与 revision 发布属于外部职责。
- 开发者固定正确的内部组件组合，测试验证事实与必需投影顺序；不增加装配校验、依赖图、深拷贝或通用 metadata schema。
- LlamaIndex 只借鉴“转换步骤顺序组合、检索器与索引解耦”的组织方式。
- LightRAG 采用“复制内核语义到本仓库”的方式，不在运行时依赖 LightRAG，也不照搬它的 workspace、多租户或存储生命周期。
- `resource_id + version/content_revision` 是当前唯一的外部来源身份。RAG 内部的 section/chunk ID 只能定位 Markdown
  副本中的本地位置。

## 0.1 必须继承的能力清单

重做时先冻结行为合同，再重新选择代码组织方式。下面的“继承”表示行为和边界必须保持，不表示继续复用当前类层次。

| 能力                   | 继承内容                                                                                                                                          | 新实现边界                                                                                                 | 验收证据                                                 |
|------------------------|---------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------|----------------------------------------------------------|
| 上游事件               | 只接收 `resource_id`、`version`、Markdown `content` 和 ACL 事件                                                                                   | 新入口只做校验和投递，不向上游索取 source unit/locator                                                     | payload contract、旧版本和重复事件测试                   |
| ContentRevision        | `resource_id`、`document_version`、完整 `content_sha256` 及派生 `content_revision`                                                                | revision 身份与 contextual、向量、图谱派产物分离                                                           | 同版本内容漂移、重建幂等测试                             |
| ACL 判权               | `PermissionScope` 群组角色派生；`ResourceAcl.can_read()` 当前顺序为 owner -> 显式 readable -> 全局 excluded -> 群组规则，并对缺失 ACL fail-closed | ACL 作为独立 capability，不放进 chunk、graph prompt 或向量正文；按现有 ACL 事件建立本地投影                | owner/白名单/排除/群组管理员/公开群组/私有群组真值表     |
| 请求内 active revision | 为每个资源首次确定 applied revision，后续正文和派生产物固定使用该 revision；ACL 继续使用本地最终一致投影                                          | retrieval、graph、outline、section read 共用轻量读取上下文，不提供跨后端原子观察点                         | active revision 变化、ACL 缺失、跨 revision 泄漏测试     |
| 发布一致性             | staged/applied、版本单调性、乐观锁、向量完整后才 apply                                                                                            | publication 只协调事实和指针，不知道 graph 查询细节                                                        | 并发 stage/apply、旧任务、半成品不可见测试               |
| 删除语义               | 先撤销可见性，再异步清理 Mongo/Qdrant/Neo4j，失败可重试                                                                                           | 删除任务独立于查询路径，不能恢复 active 指针                                                               | 重复删除、单投影失败、查询不可见测试                     |
| Markdown 结构          | `MarkdownChunker` 产出的 `Chunk`、Section、Anchor、node IDs、内部 SourceSpan                                                                      | 不重新建立 Node 层；RAG 只投影需要的结构字段                                                               | chunk 顺序、section 树、anchor、span 重建测试            |
| 普通检索               | Dense/BM25 独立召回、候选并集/Fusion、批量 Mongo 回查、rerank 和相关性门控                                                                        | 适配 `utils.retrieval`，不复制 Candidate/Fusion 算法                                                       | Dense/BM25/Fusion/rerank/ACL 集成测试                    |
| VectorStorage          | 只继承向量 upsert/delete/query 的后端原语                                                                                                         | Qdrant dense/BM25、ACL scope、revision 完整性拆到 `QdrantRetriever`、`VectorIndexer`、`ProjectionManifest` | fake storage contract、Qdrant adapter、发布完整性测试    |
| Contextual             | 连续 section/chunk 上下文、字符预算、`<context>`/`<target_chunk>`、graph target-only 分离                                                         | 作为 Chunk 检索文本变换，不成为证据系统                                                                    | 同 revision 边界、预算、target-only 测试                 |
| 图谱核心               | LightRAG prompt、抽取解析、规范化、去重、合并、local/global/hybrid/mix                                                                            | 仅替换 storage 和权限/ontology 边界                                                                        | LightRAG golden cases、本地 storage contract 测试        |
| GraphStorage           | 继承节点、关系、triplet/rel-map、批量 upsert/delete 原语                                                                                          | Neo4j traversal、ACL、revision 发布、向量候选拆到 graph adapter/writer/retriever                           | fake graph storage、Neo4j adapter、graph retrieval tests |
| Ontology/plugin        | ontology 约束、非法事实拒绝、deterministic producer、显式过滤字段                                                                                 | plugin 不再拥有通用 chunk selector/metadata builder                                                        | ontology 校验和 producer 来源测试                        |
| 读取能力               | active resource/section 读取、outline/neighborhood 的 ACL 校验                                                                                    | 只读本地副本，不提供上游精确回源                                                                           | section/outline 跨资源 ACL 测试                          |

### 不继承的架构和能力

- 当前 `domain/application/container` 的总编排形态；新实现从独立 capability package 开始，旧包只作行为参考。
- 逐 chunk metadata builder、metadata codec 注册链和通用 `chunk_selector`。
- span 级 evidence ledger、resolver、跨库证据聚合、公共 Range 和“精确回源”承诺；内部 `SourceSpan` 仍保留，详见第一阶段。
- 静态 parent/evidence block、PPR/贡献聚合、mention count/置信度累加、Kafka offset 状态和逻辑删除回滚状态机。

## 1. 目标架构

### 1.1 数据事实和投影

权威事实保持在 Mongo：

```text
DocumentRevision(resource_id, document_version, content_sha256, raw_markdown, structure)
Chunk(resource_id, content_revision, chunk_id, section_id, chunk_index, raw_text, metadata)
Chunk.metadata["entities"/"relationships"] -> GraphStorage projection
ResourceIndexState(staged_content_revision, applied_content_revision)
ResourceAcl(permission facts, local projection metadata)
```

当前 `ContentRevision` 的组成必须保持清楚：

- `resource_id`：资源命名空间，也是当前唯一的上游来源身份；
- `document_version`：上游事件携带的单调文档版本，用于旧事件丢弃、staged/applied 条件更新和幂等判断；
- `content_sha256`：RAG 对收到的 Markdown 副本计算的完整 SHA-256，用于确认同一版本的内容不可变、检测同版本内容漂移和重建一致性；
- `content_revision`：派生显示/索引键，当前格式为 `resource_id@document_version#content_sha256[:16]`
  。它不是新的来源事实，也不能单独替代上面三个字段。

ACL 不属于 `ContentRevision`：ACL 使用独立的 `ResourceAcl` 事实；上游是否提供版本字段不作为 RAG V4 的既定前提，本地不得通过
`updateTime` 或自定义时间戳伪造上游顺序。`metadata["contextual_context"]`、向量、图谱事实和图谱投影也不属于 revision
身份，它们都是可重建的派生产物。

Qdrant 和 Neo4j 只是可重建投影：

- Qdrant 保存 chunk、graph node、graph edge 的向量及最低限度的 `resource_id/content_revision/id` 过滤字段。
- Neo4j 保存图拓扑和用于预过滤的资源权限字段。
- 查询必须在 Mongo 批量回查时重新确认 active revision 和 ACL；任何投影都不能成为最终权限或正文来源。

### 1.2 能力模块

新的编排只保留以下能力模块，模块内部可以有普通 Python 类和函数，不再维护跨模块的空泛 service 层：

1. `ingestion`：调用 `MarkdownChunker`，构造 `Document` 和 `Chunk`，执行 `IngestionPipeline`。
2. `contextual`：作为 Transformer 基于 Chunk 序列生成 `metadata["contextual_context"]`。
3. `retrieval`：调用文档向量仓储、`utils.retrieval` Fusion/ranking、Mongo 批量回查和 ACL 快照。
4. `graph`：LightRAG 抽取/合并/查询内核，加上 ontology、plugin 和本地来源关联。
5. `publication`：staged/applied 条件更新、删除和投影重建任务。
6. `acl`：权威 ACL 读取、本地 ACL 同步和请求级可见性判断。

HTTP、Kafka 和依赖注入只负责把外部请求接到这些能力；不在 transport 或 container 中重新实现业务规则。

### 1.3 存储原语与检索器分离

参考 LlamaIndex `VectorStore`、`GraphStore`/`PropertyGraphStore` 的职责边界，但继续使用本地 `Chunk`、`GraphNode`、
`GraphEdge` 和 `utils.retrieval`，不引入 LlamaIndex Node 模型。

#### VectorStorage

`VectorStorage` 只表示一个可写、可删、可查询的向量后端，最小职责为：

```python
class VectorStorage(Protocol):
    async def upsert(self, records: Sequence[VectorRecord]) -> WriteReceipt: ...
    async def delete(self, selector: VectorSelector) -> None: ...
    async def query(self, query: VectorQuery) -> list[VectorHit]: ...
```

其中 `VectorRecord`、`VectorQuery`、`VectorHit` 是不包含 Qdrant 类型、ACL 对象或 Chunk 业务字段的本地存储值对象；
`VectorQuery` 只表达 embedding/text、query mode、top_k 和通用 metadata predicates。

以下内容不进入 `VectorStorage`：

- `search_dense`、`search_bm25` 这种 Qdrant collection/field 语义；
- `PermissionScope`、`ResourceAcl` 和租户授权判断；
- `write_revision` 这种 DocumentRevision 编排；
- `is_complete` 这种某次 Qdrant 写入后的发布校验；
- embedding 生成、rerank、Fusion 和 `Candidate` 转换。

职责重新分配如下：

- `QdrantVectorStorage`：实现 `upsert/delete/query`，处理 Qdrant collection、dense/sparse field 和 payload 序列化。
- `QdrantRetriever`：选择 dense/BM25 查询模式，构造 ACL/资源/插件过滤条件，调用 `VectorStorage.query()`，将 `VectorHit` 转为
  `utils.retrieval.Candidate`；`search_dense` 和 `search_bm25` 属于这里，而不是 storage protocol。
- `VectorIndexer`：选择 Chunk 检索文本，生成 embedding 和 `VectorRecord` 并写入 Chunk vector
  storage；不负责检索。Entity/Relationship 向量由 GraphIndexer 生成。
- `ProjectionReceipt`/`ProjectionManifest`：记录本次 revision 期望的 record IDs 和写入回执；发布流程用它判断投影是否完整，不调用通用
  storage 的 `is_complete()`。
- `HybridRetriever`：组合 Qdrant dense/BM25 retriever、Fusion、Mongo 回查、ACL 快照和 reranker；不直接依赖 Qdrant client。

#### GraphStorage

`GraphStorage` 只表示图拓扑/属性存储原语，最小职责为：

```python
class GraphStorage(Protocol):
    async def get_nodes(self, selector: GraphNodeSelector) -> list[GraphNode]: ...
    async def get_relations(self, selector: GraphRelationSelector) -> list[GraphEdge]: ...
    async def get_rel_map(self, request: GraphTraversalRequest) -> list[GraphTriplet]: ...
    async def upsert_nodes(self, nodes: Sequence[GraphNode]) -> WriteReceipt: ...
    async def upsert_relations(self, edges: Sequence[GraphEdge]) -> WriteReceipt: ...
    async def delete(self, selector: GraphSelector) -> None: ...
```

`GraphStorage` 不知道 `PermissionScope`、`ResourceAcl`、`content_revision` 的发布状态、向量候选、reranker 或
ontology。资源/revision/ACL 作为存储记录的可过滤属性存在，但本地 ACL 判权和请求内 selected revision 由 `GraphRetriever`
/read capability 决定。

职责重新分配如下：

- `Neo4jGraphStorage`：实现节点、关系、triplet/rel-map、批量 upsert/delete 和 provider-specific query adapter。
- Chunk 中持久化的 `entities`/`relationships` metadata：保存 LLM 抽取结果并作为无需重跑 LLM 的重建输入；`GraphIndexer`
  将节点、关系和 source Chunk ID 投影到 Neo4j 与 Qdrant，不增加独立 `GraphFactStore`。
- `GraphIndexer`：从 Chunk metadata 和确定性结构事实装配图谱，写入 `GraphStorage` 和图向量 storage；将来源 Chunk
  关联纳入可重建投影；处理 revision replacement 和重建，不进入 GraphStorage protocol。
- `GraphRetriever`：组合 node/edge retriever、`GraphStorage.get_rel_map()`、本地 ACL/selected revision、ontology
  filters、有限遍历和 rerank；当前 `GraphTopologyRepository.traverse(candidates, scope, resource_ids, ...)` 必须拆散。
- `GraphQuery`/`GraphTraversalRequest`：表达通用图查询参数；`seed_node_ids`、方向、深度、关系类型和节点类别属于 retriever
  query，不把 HTTP request 直接下传到 storage。

#### 三类边界的判断规则

- 能在不同向量/图数据库上成立的 CRUD/query 原语，进入 storage。
- 依赖 Qdrant/Neo4j 的字段、索引、查询模式，留在 provider adapter 或对应 retriever。
- 依赖 resource/revision/ACL/ontology/发布状态的策略，留在 RAG capability 编排。

因此，抽象先从真实的第二个实现或明确的 provider 替换需求反推；不会为了“看起来通用”把当前 Qdrant/Neo4j 的全部方法抬升到
protocol。

当前接口的迁移映射：

| 当前伪抽象                                              | V4 处理                                                                                                       |
|---------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| `DocumentVectorRepository.write_revision()`             | `VectorIndexer`，由 Chunk -> VectorRecord 的投影层负责                                                        |
| `DocumentVectorRepository.search_dense/search_bm25()`   | `QdrantRetriever` 的两个查询策略，底层只调用 `VectorStorage.query()`                                          |
| `DocumentVectorRepository.is_complete()`                | `ProjectionManifest`/`WriteReceipt` 的 revision 发布校验                                                      |
| `DocumentVectorRepository.delete_resources()`           | `ProjectionLifecycle` 或 storage selector 删除，不属于检索 protocol                                           |
| `GraphTopologyRepository.replace_revision()`            | `GraphIndexer`                                                                                                |
| `GraphTopologyRepository.traverse()`                    | `GraphRetriever` 组合 storage rel-map、本地 ACL、selected revision、过滤和 rerank                             |
| `GraphFactRepository`                                   | 删除独立图事实仓储；从 Mongo Chunk metadata 重建 GraphStorage 和图向量投影                                    |
| `GraphNodeVectorRepository`/`GraphEdgeVectorRepository` | 同一个 `VectorStorage` 原语的不同 collection/index 配置，由 node/edge retriever 和 projection writer 赋予语义 |

## 2. 第一阶段：保留内部 span，删除伪精确证据体系

### 2.1 保留的本地字段

`Chunk` 保留：

- `chunk_id`：由 `resource_id + content_revision + utils.markdown` 生成的 chunk 身份；
- `resource_id`、`content_revision`、`chunk_index`；
- `section_id`、`section_path`、`anchor_labels`；
- `raw_text`、`content_token_count`；
- 普通 `metadata`，由 Transformer 和 Indexer 按约定读写；

`section_id/chunk_id` 的文档字符串、API 描述和测试必须明确它们是 RAG 副本的本地标识。

### 2.2 SourceSpan 的保留边界

`SourceSpan` 不删除。它是 `utils/markdown` 解析和分块的内部结构事实，当前用于：

- `MarkdownNode` 的语法节点范围；
- `Section.own_span`、`Section.subtree_span`、`Section.content_spans`；
- `Anchor.source_span`；
- contextual 连续窗口和 outline/调试所需的副本内位置计算；
- 必要时对同一 Markdown revision 做内部重建、诊断和缓存适配。

RAG 可以继续在 `DocumentStructure` 中保存 `sections` 和 `anchors`，因为它们属于该 Markdown 副本的结构快照；`Chunk` 是否直接保存
`source_spans` 继续由 `utils.markdown` 到 RAG 的最小字段合同决定，默认不复制到 Chunk，避免让 chunk 的通用合同依赖字符范围。

### 2.3 删除或按需冻结的证据能力

不再把下面能力作为普通 RAG 的强制链路：

- 基于 span 的外部精确引用或“回到上游原文”的承诺；
- 以 offset 为公共协议的任意 Range 读取；
- 逐 chunk evidence ledger、evidence resolver、字符级 source attribution；
- 为了给 span 绑定出处而增加的跨库 evidence 聚合、证据状态机和 resolver。

`SourceSpan` 可以继续存在于 Common 的结构对象和 RAG 的 `DocumentRevisionEntity.sections/anchors` 中，但不得进入 Qdrant
payload、Neo4j 来源 Chunk ID 属性、HTTP 引用响应或 LLM 引用结果，除非未来出现明确的本地调试/缓存消费者。任何新增 span
消费必须先证明不改变外部引用语义、不引入跨库证据编排。

### 2.4 轻量本地引用

保留一个小的不可变值对象即可，不建立强制 evidence 子系统：

```python
@dataclass(frozen=True, slots=True)
class LocalCitation:
    resource_id: str
    content_revision: str
    chunk_id: str | None = None
    section_id: str | None = None
    section_path: list[str] = field(default_factory=list)
```

规则：

- `resource_id + content_revision` 表示可验证的资源版本；
- `chunk_id/section_id/section_path` 只表示 Markdown 副本内的本地阅读位置；
- 没有 chunk 的 graph producer 事实只能返回资源版本引用；
- `LocalCitation` 可以出现在检索和图谱结果中，但不能叫“原文精确引用”。

图事实来源由 Chunk metadata 中事实的所属 Chunk 隐含表达；GraphIndexer 将来源 Chunk 关联写入 Neo4j 图投影，GraphRetriever
使用 `resource_id/content_revision/chunk_id` 回查 Mongo Chunk。这个来源只表示本地 Chunk，不承载字符定位，也不是外部
evidence contract。

## 3. 第二阶段：用现有工具恢复 ingestion 和 contextual

### 3.1 摄入链路

实现唯一的基本摄入流程：

```text
DocumentReady(resource_id, version, markdown)
  -> MarkdownChunker
  -> Document + list[Chunk]
  -> Mongo staged revision
  -> contextual transform（可配置启用）
  -> Qdrant 文档投影
  -> applied revision
```

`DocumentPreparer` 只负责结构转换和 staged 写入，不再接收 `chunk_metadata_builder`。

具体调整：

- 直接消费 `MarkdownChunker` 的 `sections/chunks/anchors`，只做 resource/revision ID 命名空间重写。
- 删除 `DocChunkMetadata`、`GeneralChunkMetadata`、逐 chunk metadata codec 和 builder；保留普通 `dict` metadata 作为
  Transformer 与 Indexer 的阶段间载体，插件元数据仍由 Document metadata 或 graph fact 的显式字段承载。
- 删除 `RagPlugin.chunk_selector`。所有文档 chunk 默认进入文档索引；图谱是否抽取由 graph capability 的 bounded
  configuration 控制，不能由任意 predicate 静默改变文档索引事实。
- `retrieval_text` property 只把 `metadata["contextual_context"]` 与 `raw_text` 局部拼接，不将其误认为证据或正文。

### 3.2 Contextual transform

保留一个以 Chunk 序列为输入和输出的 Transformer，不新增 Node 或第二套通用 transform 框架：

```python
class Contextualizer(Protocol):
    async def transform(self, chunks: list[Chunk]) -> list[Chunk]: ...
```

行为固定为：

- 优先取同一 section 的连续 Markdown 内容；
- section 内容过大时，取同一 section 内以 `chunk_index` 为序的相邻 chunk 窗口；
- 不跨 `resource_id`、`content_revision` 或 section 边界；
- 使用既有 contextual prompt 和并发/缓存策略；
- 产物为 `<context>...</context>` 后接 `<target_chunk>...</target_chunk>`；
- context 只进入 dense/BM25 检索文本，不进入 citation，也不改变 chunk 身份。

图谱抽取单独调用 target-only context builder，只把目标 chunk 和 section breadcrumb 交给 LightRAG 抽取 prompt，不能复用普通检索的
contextual prefix。

完成后删除 `ContextMigrationRequired`、`_generate_retrieval_context` 的占位异常和
`HybridRetriever._build_dynamic_parents` 的过渡失败分支。

## 4. 第三阶段：收敛普通 RAG 检索

### 4.1 检索数据流

普通检索固定为：

```text
query
  -> Qdrant dense candidate + Qdrant BM25 candidate
  -> 组装 utils/retrieval FusionRetriever，当前使用RoundRobinFusion
  -> 批量 Mongo 读取 Chunk/Document
  -> selected active revision + local ACL decision
  -> 现有 reranker / relevance gate
  -> list[ChunkHit]
  -> 连续阅读上下文组装
  -> list[DynamicParent + LocalCitation]
```

不再引入新的候选类型或包装层：

- 后端适配器把结果转换成 `utils.retrieval.Candidate`；
- Fusion 使用现有 `UnionFusion`/`RrfFusion`；
- 最终排序继续使用现有 `RankedCandidate` 及 RAG 自己的 `ChunkHit` 转换；
- Qdrant/Neo4j 不直接返回正文；正文和引用由 Mongo chunk 批量组装。

### 4.2 上下文和响应

- `DynamicParent` 继续是模型消费的连续文本，不升级为新的 node 类型。
- 每个 parent 携带一个或多个 `LocalCitation`；相同 resource/revision/chunk 合并去重。
- graph seed node 仍可附在 parent 上，但 seed node 不是 evidence，也不替代 LocalCitation。
- 读取 API 继续只按 resource/section/chunk 本地 ID 读取当前 active Markdown，不增加 Range 接口。

### 4.3 编排简化

删除当前总 container 中对所有能力的直接构造；改为按能力提供少量 factory/provider：

- document factory：chunker、document repository、chunk repository、contextual builder；
- retrieval factory：vector repositories、fusion、reranker、request read context；
- graph factory：graph core、ontology registry、fact repository、projection writers；
- application.publication 装配：资源变更到可检索状态的用例、流程状态及内容依赖；ACL 保持独立能力。

factory 只组装依赖，不持有请求状态、不承担流程判断。Kafka handler 和 FastAPI endpoint 只调用能力入口。

## 5. 第四阶段：复制 LightRAG 图谱内核

### 5.1 复制范围

从 `services/rag/references/LightRAG` 复制并本地化以下语义：

- entity/relation extraction system、user、continue prompt；
- keyword extraction prompt；
- 结构化/文本抽取结果解析和大小上限；
- entity/relation 名称规范化、同名去重、描述合并和 source chunk ID 合并；
- local/global/hybrid/mix 查询的候选构建、轮询合并、token 截断和 query context 组装；
- LLM response cache 的 key 语义，若当前配置需要缓存则接入现有 RAG cache adapter。

不复制：

- LightRAG `LightRAG` 总运行时和 workspace 隔离作为租户边界；
- LightRAG 文档状态、文件路径和 sidecar 管理；
- 其 storage 实现、Redis/NetworkX 运行时假设；
- 与本地 ACL、revision、ontology 无关的兼容层。

### 5.2 本地接口

图谱内核只需要现有 RAG 仓储的最小能力：

- 读写 revision 内的 graph node/edge topology 和来源 Chunk 关联投影；
- 按 node/edge/chunk ID 批量读取；
- 写入 Neo4j topology 和 Qdrant graph projections；
- 以 `resource_id/content_revision` 作为所有事实和投影的过滤边界。

图谱事实来源：

- LLM 抽取事实保存在目标 Chunk metadata，图投影记录来源 Chunk 关联；
- deterministic producer 事实不伪造 chunk 来源，只记录其所属 plugin_id 和资源版本；
- graph query 从图投影取得来源 Chunk ID，回查 Mongo Chunk 后生成 `LocalCitation`。

### 5.3 Ontology 和 plugin

- ontology 继续声明实体类别、关系类型、端点约束和允许的 metadata/filter 字段。
- ontology 描述作为 LightRAG extraction prompt 的约束片段，同时对解析结果做硬校验。
- 无效事实直接丢弃；只保留一个有上限的关系端点修复步骤，不建立通用 evidence resolver。
- plugin 只暴露 ontology、deterministic producer 和显式 graph query filter；不再控制通用 chunk metadata 或文档 chunk
  是否进入索引。

### 5.4 ACL 位置

- ACL 不写进 LLM prompt。
- Qdrant/Neo4j 只做 resource 级预过滤。
- Mongo 回查 graph fact/source/chunk 时重新检查 active revision 和请求 ACL；任何一项缺失即丢弃候选。
- graph node/edge 的跨资源合并只在查询结果层完成，不能把不同 resource 的权限事实写成一个共享权威实体。

## 6. 第五阶段：重构落地与旧实现删除

### 6.1 迁移策略

不在旧 `src/rag` 上逐文件打补丁。旧实现冻结为行为参考，新实现先在独立 capability package
中完成。此次是无线上调用、无兼容要求的完整重构，不做双写、灰度、旧数据迁移、双版本结果对照或旧 schema 清理窗口：

1. 建立新的 ingestion/retrieval/graph/ACL package，以及 application.publication 用例；旧 package 在此阶段不接收新功能。
2. 先实现并测试 ACL、ContentRevision、staged/applied 和 MarkdownChunker 投影，这些是所有后续能力的底座。
3. 以固定 fixture、行为合同和 LightRAG 原始参考用例验收新能力；旧实现只用于定位既有业务语义，不要求新旧结果逐项兼容。
4. 普通检索、读取、图谱生产与查询分别通过 capability 验收后，在同一重构交付中替换旧实现及其数据模型；按新模型从上游现有内容和
   ACL 事件重建本地投影。
5. 删除旧 `src/rag` 流程、无消费字段和旧投影定义，不保留兼容桥或过渡运行窗口。Kafka 消费与重试已按 15 增量提前接入；HTTP
   内容能力与完整生产 Pipeline 装配仍待后续。

新 package 的临时目录和切换规则：

- 当前代码直接放在 `services/rag/src/rag`，publication 作为 application 下的用例模块，事件消费者也归该模块；旧
  services/wisepen-rag-service 仅作源码参考，不作为运行时依赖。
- 旧 `src/rag` 只允许作为阅读参考，不被新 package 导入；新 package 不复制旧 container、domain/application 分层。
- Kafka 已直接调用新用例，ACL 与 publication 共用 RAG Kafka 后端；不经过旧流程。HTTP 内容能力的 transport 继续留到后续阶段。

### 6.2 删除顺序

按依赖反向删除：

1. 删除 API/模型对 span、offset、Range 的外部引用；保留 `DocumentRevision` 内部结构快照中的 span。
2. 删除 `chunk_metadata_builder`、`chunk_selector`、metadata codec 和注册表分支。
3. 删除旧 contextual migration exception 和未完成的 dynamic parent 过渡逻辑。
4. 删除旧自定义 graph extraction/merge，替换为本地 LightRAG core。
5. 清理 Mongo schema、Qdrant payload 和 Neo4j 属性中的无消费字段。
6. 最后缩减 container、README 和测试中的旧架构描述，并补充 span 的内部保留边界。

## 7. 文件级实施清单

先建新实现，不修改旧流程文件：

- `services/wisepen-rag-service/src/rag_v4/acl/`：复制并冻结 `PermissionScope`、`ResourceAcl.can_read()`、ACL
  仓储端口和本地最终一致投影语义。
- `services/wisepen-rag-service/src/rag_v4/ingestion/`：封装 `utils.markdown.MarkdownChunker` 到 `Document/Chunk`
  的投影，保留内部 sections/anchors/spans，并由 `IngestionPipeline` 组合后续阶段。
- `services/rag/src/rag/application/publication/`：收敛为 models、store、publish 三个实现文件；publish
  定义事件、消费者和完整发布操作。消费者直接调用 publisher.publish 或 destroy_resources，摄入、版本判断、外部
  Pipeline、内容回查及条件发布归入同一用例；必需投影完整性依赖 Indexer 成功合同。内容与物理销毁消费者已接入，物理清理后续实现；销毁先永久撤销可见性。
- `services/rag/src/rag/backends/kafka/`：ACL 与 publication 共用的有限重试、延迟回投、DLQ 和精确分区确认；业务校验和幂等仍由用例负责，不修改
  common.kafka。
- `services/wisepen-rag-service/src/rag_v4/contextual/`：实现 `Transformer.transform(chunks)` 的连续上下文变换及 graph
  target-only builder。
- `services/wisepen-rag-service/src/rag_v4/retrieval/`：适配 Qdrant dense/BM25 和 `utils.retrieval` 的
  Candidate/Fusion/RankedCandidate，返回本地 chunk 引用。
- `services/wisepen-rag-service/src/rag_v4/graph/`：放入本地 LightRAG prompt、抽取解析、合并、查询、ontology 和 plugin 边界。
- `services/wisepen-rag-service/src/rag_v4/storage/`：定义最小 `VectorStorage`、`GraphStorage` 及
  `VectorRecord/VectorQuery/GraphTraversalRequest` 值对象；不放 Qdrant/Neo4j 专有方法。
- `services/wisepen-rag-service/src/rag_v4/adapters/`：Mongo、Qdrant、Neo4j、LLM、embedding、reranker 适配器；适配器不向上泄漏供应商类型。
- `services/wisepen-rag-service/src/rag_v4/indexing/`：`VectorIndexer`、`GraphIndexer` 和必要的 projection
  manifest；发布完整性检查不放进 storage protocol。
- `services/wisepen-rag-service/src/rag_v4/api/` 与 `composition.py`：最后接入 HTTP/Kafka，保持入口薄化。

旧 `services/wisepen-rag-service/src/rag/` 的对应模块只做参考和对照测试；新实现验收后整体替换入口，再删除旧流程和无消费实体。

## 8. 验收标准

### 合同验收

- 上游 payload schema 没有新增字段要求。
- `Chunk` 是检索、contextual 和 graph extraction 的唯一文本基本单元。
- 对外响应、Qdrant payload、Neo4j source Chunk ID properties 不出现 span/offset；Mongo
  `DocumentRevisionEntity.sections/anchors` 可以保留内部 span 快照。
- 所有本地 ID 都有明确的 local-copy 语义。

### 行为验收

- 同一 resource/version 的 chunk、contextual 文本和 graph source 可稳定重建。
- contextual 只改变检索文本，不改变 chunk 身份，不把邻居文本归因给目标图事实。
- section/chunk 上下文扩展不会跨 resource、content revision 或 section 边界。
- dense、BM25、Union/RRF、rerank、ACL、active revision 和 Mongo 批量回查形成可运行闭环。
- graph local/global/hybrid/mix 查询行为与 LightRAG 参考实现的 golden cases 一致。
- ontology 能阻止非法 node/edge；deterministic producer 不伪造 chunk 证据。
- 图谱投影失败不改变文档 active revision；旧 revision 查询不会泄漏到新 revision。
- `VectorStorage` 的 fake 实现可以在不提供 `search_dense`、`search_bm25`、`is_complete` 的情况下通过 storage
  contract；这些能力只在 Qdrant retriever/projection tests 中出现。
- `GraphStorage` 的 fake 实现可以完成节点、关系、rel-map 和 upsert/delete contract，而不需要 ACL、HTTP request、active
  revision 或向量候选；这些策略只在 GraphRetriever/GraphIndexer tests 中验证。

### 能力验收顺序

按依赖关系执行，前一层未通过不得进入后一层：

1. **ACL 与 revision 底座**：先完成 `PermissionScope`/`ResourceAcl.can_read()` 真值表、本地 ACL 投影、请求内 selected
   revision、staged/applied 乐观锁、旧版本和删除语义。
2. **Markdown 与 chunk**：验证 `MarkdownChunker` 投影、chunk 稳定 ID、section/anchor/span 内部结构和本地引用。
3. **Contextual**：验证连续 section/chunk 窗口、预算、缓存/并发和 graph target-only 分离。
4. **普通检索**：验证 dense、BM25、`utils.retrieval` Fusion、rerank、批量回查和 ACL 二次校验。
5. **读取能力**：验证 section、outline、neighborhood 只读 active 且可读的副本。
6. **图谱**：最后验证 LightRAG prompt/merge/query、ontology、producer、图谱投影和 graph source 回查。

ACL 的最小真值表必须覆盖：资源 owner、显式 readable user、全局 excluded user、群组
owner/admin、已加入公开群组、公开群组排除、已加入私有群组可读、私有群组未授权、未加入群组、ACL 缺失，以及 active revision
在读取期间变化。对每个 case 同时验证向量预过滤和 Mongo 最终判权，确保预过滤错误不会扩大可见范围。

### 工程验收

- 先修复当前 pytest 收集所需的本地 `wisepen-common` 依赖解析和缺失的 `context_expander` 实现。
- 每阶段运行对应 focused tests、`compileall`/类型检查和 `git diff --check`。
- 完成后执行 RAG service 全量测试，并单独执行 LightRAG 内核 golden tests 和 ACL/revision 故障矩阵。

## 9. 明确不做

- 不修改上游资源事件，不要求 source unit、外部 locator、回源 API 或结构化 Markdown 清单。
- 不把 RAG 做成 LlamaIndex 的兼容实现。
- 不引入 Node、NodeWithScore、BaseNodePostprocessor 或第二套检索模型；写入侧只使用已确定的五类核心 Protocol。
- 不保留任意垂类逐 chunk metadata 扩展机制；普通 `dict` metadata 仅作为 pipeline 阶段间传递载体。
- 不把 chunk selector 作为插件任意过滤入口。
- 不做字符级“精确回源”、公共 Range、PPR/贡献聚合、Evidence resolver 或第二套强制证据账本；内部 SourceSpan
  保留给解析、上下文、outline、调试和未来明确的本地消费者。
