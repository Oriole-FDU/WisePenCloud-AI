# RAG V4 设计文稿

## 1. 文档目的和范围

本文是 RAG V4 的实现设计合同，整合 00–10 方案中已经确认的边界。它描述核心模型、写入管线、存储端口、普通检索、图谱能力、权限和版本语义。

本设计不包含 HTTP 路由、MCP 工具注册和最终 transport composition。MCP 只作为可信身份来源的既有边界被引用：最终用户身份由可信安全上下文注入，RAG application 接收 `PermissionScope`，不从业务参数读取用户 ID。

本设计也不追求 Mongo、Qdrant、Neo4j 的全局强一致。它只消除会造成错误的内容混版、越权访问、跨版本图谱读取和不受控的索引语义漂移。

## 2. 核心设计结论

### 2.1 五类核心抽象

写入侧只有五类核心抽象：

```text
Document
  -> Chunker.split(document)
  -> list[Chunk]
  -> Transformer.transform(document, chunks) ...
  -> Indexer.index(chunks) ...
  -> Storage

Retriever
  <- Storage Reader
```

| 抽象 | 负责 | 不负责 |
|---|---|---|
| `Chunker` | Document 分块、边界、顺序和结构归属 | Embedding、图谱抽取、持久化 |
| `Transformer` | 对完整 Chunk 列表做内容加工或知识增强 | Storage、Embedding、持久化 |
| `Indexer` | 选择索引对象、生成投影并调用 Writer | 底层 provider CRUD、检索策略 |
| `Storage` | 通用向量/图存取原语 | Chunk 业务、ACL、revision、检索模式 |
| `Retriever` | 查询构造、候选生成、组合和读取回查 | 索引构建和持久化 |

`IngestionPipeline` 只是顺序编排器，不是新的业务仓储层、事务协调器或错误补偿器。

### 2.2 最小接口

```python
class Chunker(Protocol):
    async def split(self, document: Document) -> list[Chunk]: ...


class Transformer(Protocol):
    async def transform(self, chunks: list[Chunk]) -> list[Chunk]: ...


class Indexer(Protocol):
    async def index(self, chunks: list[Chunk]) -> None: ...
```

```python
class IngestionPipeline:
    def __init__(
        self,
        *,
        chunker: Chunker,
        transformers: list[Transformer],
        indexers: list[Indexer],
    ):
        self.chunker = chunker
        self.transformers = transformers
        self.indexers = indexers
        self._validate_assembly()

    async def run(self, document: Document) -> None:
        chunks = await self.chunker.split(document)
        for transformer in self.transformers:
            chunks = await transformer.transform(chunks)
        for indexer in self.indexers:
            await indexer.index(chunks)
```

接口约束如下：

- `Document` 只进入 `Chunker.split()`；
- Transformer 和 Indexer 接收完整 `list[Chunk]`；
- Transformer 可以原地修改 Chunk，也可以返回新 Chunk；Pipeline 始终使用返回值；
- Pipeline 不自动深拷贝，不要求不可变 Chunk；
- Chunk identity、resource/revision 来源、顺序和 raw text 必须保持稳定；
- Indexer 只读共享 Chunk，不使用 `pop()` 消费 metadata；
- Pipeline 按配置顺序串行执行；组件内部可以自行批处理或并发；
- 任一未处理异常直接传播；Transformer 失败停止后续阶段，Indexer 失败停止后续 Indexer，已经完成的写入保留；
- Pipeline 不提供回滚、`continue_on_error`、跨 Indexer 并行或统一 failure object；
- 只有所有阶段成功返回，部分索引可见性和 revision 发布由外部 capability 决定。

### 2.3 装配完整性

装配入口只做静态、直接的检查，不增加依赖图或新的注册抽象：

- `GraphExtractor` 存在时必须存在消费其结果的 `GraphIndexer`；
- Entity/Relationship embedding 配置存在时必须有对应 VectorWriter；
- `graph_enabled=False` 的外部 Pipeline 配置不包含图谱组件；
- 错误装配在构造或启动阶段报错，不能运行后静默丢弃 metadata。

## 3. Document、Chunk 和 metadata

### 3.1 Document

`Document` 是分块入口，至少表达：

```text
resource_id
document_version
content_revision
raw_markdown
structure
metadata
```

Document metadata 继续作为垂类扩展和 Pipeline 组装依据。Document 级策略由上层选择 Pipeline 配置，不由 GraphExtractor 或 GraphIndexer 自行判断。

`graph_enabled` 是上层可采用的策略输入示例，不是 `Document` 必须包含的核心字段。外部装配逻辑读取所需业务元数据后选择并复用对应 Pipeline；组件不读取此开关，也不把它复制到 Chunk。

不要求将全部 Document metadata 复制到每个 Chunk。只有后续 Chunker、Transformer 或 Indexer 实际需要的结构信息才传递到 Chunk 字段或普通 metadata。

### 3.2 Chunk

```python
@dataclass(slots=True)
class Chunk:
    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None
    section_path: list[str]
    raw_text: str
    content_token_count: int
    anchor_labels: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = str(self.metadata.get("contextual_context") or "").strip()
        return f"{context}\n\n{self.raw_text}" if context else self.raw_text
```

`metadata` 是普通 `dict[str, object]`，依赖开发者遵守约定，不引入 Pydantic 模型、metadata 注册表或通用 schema 校验。

约定键为：

```python
metadata["contextual_context"]  # 检索上下文，可缺失
metadata["entities"]            # GraphExtractor 的实体结果
metadata["relationships"]       # GraphExtractor 的关系结果
```

metadata 不得改变 Chunk identity、顺序、ACL 或 active revision。图谱启用时，`entities` 和 `relationships` 是随 Chunk 持久化的可重建抽取事实；其来源就是所属 Chunk，不再复制到独立 GraphFactStore/GraphChunkSource 集合。`contextual_context` 属于可重建的检索派生值，不要求作为图谱事实持久化。

## 4. 权威事实、版本和权限

### 4.1 身份边界

平台保证 resource ID、user ID 和 group ID 全局唯一。RAG 不增加 tenant/workspace 维度。

用户身份只来自可信安全上下文。MCP adapter 将其转换为：

```python
PermissionScope(
    user_id=trusted_user_id,
    group_roles=trusted_group_roles,
)
```

application、repository、Retriever 和 GraphRetriever 不解析 HTTP header、MCP metadata 或用户传入的身份字段。

### 4.2 ACL

上游 Resource Service 是 ACL 权威。RAG 只消费现有 `AclRecalculateMessage`，读取当时可见的 ACL 状态，完整替换本地 `ResourceAcl`。

现有事件只是刷新触发，不证明上游 `computedGroupAcls` 已完成重算。因此本地 ACL 允许短暂最终一致：

- 新授权可能延迟生效；
- 新撤权可能存在短暂安全风险窗口；
- RAG 不使用时间戳、伪造版本、等待或重复读取猜测上游完成语义。

查询中所有结果必须经过同一个 `ResourceAcl.can_read(scope)`。Qdrant/Neo4j ACL 条件只能做候选预过滤，不能取代最终判权。没有本地 ACL 投影时不能直接判定资源可读。

### 4.3 ContentRevision

```text
resource_id        上游资源全局身份
document_version   上游内容版本
content_sha256     RAG 对完整 Markdown 计算的摘要
content_revision   上述字段组成的本地组合键
```

同一版本同一 hash 的事件幂等；同一版本不同 hash 是内容合同冲突，拒绝覆盖；低版本事件不能恢复旧 staged/applied；高版本才创建新 staged revision。

DocumentRevision、Chunk 和各类向量/图投影都必须携带 `resource_id/content_revision`。一次能力调用中，资源第一次确定 applied revision 后，后续正文、Chunk 和投影回查都固定使用该 revision。active 期间变化不触发重试或切换。

### 4.4 staged/applied

staged 是后台构建中的候选，不对用户可见。只有正文/结构、Chunk 集合、普通检索所需向量投影和 staged 条件仍成立时，外部 publication capability 才能切换 applied。

图谱是附加能力。GraphIndexer 失败不会回滚已完成的普通向量写入；是否允许该 revision 成为普通检索 active 由外部发布策略决定，不由 Pipeline 伪造跨存储原子性。

删除先撤销 staged/applied 可见性，再异步清理 Mongo Chunk、Qdrant 和 Neo4j。残留投影不能通过 active revision、ACL 或 Chunk 来源回查返回。

## 5. 写入管线

### 5.1 Chunker

Chunker 封装现有 `MarkdownChunker`，负责分块边界、chunk_index、section 归属、anchor labels 和本地 Chunk identity。它不调用 LLM、Embedding、Storage 或 Mongo。

### 5.2 Transformer

#### Contextualizer

Contextualizer 使用完整 Chunk 列表建立同一 section 内的连续上下文，在字符预算内写入 `metadata["contextual_context"]`。它不修改 raw text 和 Chunk identity，不跨 resource、revision 或 section 边界。

#### GraphExtractor

GraphExtractor 接收 ontology 和完整 Chunk 列表，从 raw text 及允许的背景上下文提取 LightRAG 风格 Entity/Relationship，写入：

```python
chunk.metadata["entities"]
chunk.metadata["relationships"]
```

它不访问 GraphStorage 或 VectorStorage，也不负责实体归一、关系去重和图投影写入。没有抽取结果可以作为成功结果写入空 metadata；真正异常必须传播并终止 Pipeline。

### 5.3 Indexer

#### VectorIndexer

VectorIndexer：

1. 选择 `chunk.retrieval_text`；
2. 调用 embedder；
3. 生成 `VectorRecord`；
4. 调用 Chunk collection 对应的 `VectorWriter.upsert()`；
5. 必要时按 projection 规则删除旧记录，但不承担查询策略。

#### ChunkFactsIndexer

完整 ingestion 配置包含一个具体的 `ChunkFactsIndexer`，把 Transformer 返回的 Chunk（包括普通 dict metadata）写入既有 Mongo Chunk 内容事实集合。它是 `Indexer` 的一个实现，不增加新的核心抽象或通用 Mongo Storage；必须在其他投影 Indexer 前成功，使图谱抽取结果可以在进程退出后用于重建。

#### GraphIndexer

GraphIndexer：

1. 从 Chunk metadata 收集实体和关系；
2. 从 Chunk 结构字段生成确定性结构关系；
3. 统一执行实体归一、关系去重和 Ontology 校验；
4. 调用 `GraphWriter.upsert_nodes()` / `upsert_relations()`；
5. 按配置调用 Entity/Relationship VectorWriter；
6. 将每个图事实关联到其来源 Chunk ID，并据此写入 GraphStorage 投影；来源事实本身保留在 Chunk metadata，不额外写 Mongo 图事实集合。

GraphIndexer 不实现 Neo4j、Qdrant 或 Mongo CRUD，不实现 local/global/hybrid 查询。

## 6. Storage

### 6.1 Reader/Writer 隔离

Reader/Writer 是依赖能力约束，不是额外仓储层。同一个 Adapter 可以实现 Reader 和 Writer。

```python
class VectorReader(Protocol):
    async def query(self, request: VectorQuery) -> list[VectorMatch]: ...


class VectorWriter(Protocol):
    async def upsert(self, records: Sequence[VectorRecord]) -> WriteReceipt: ...
    async def delete(self, filters: MetadataFilters) -> DeleteReceipt: ...


class GraphReader(Protocol):
    async def get_nodes(...): ...
    async def get_relations(...): ...
    async def get_rel_map(self, request: GraphTraversalQuery) -> list[GraphPath]: ...


class GraphWriter(Protocol):
    async def upsert_nodes(self, nodes: Sequence[GraphNode]) -> WriteReceipt: ...
    async def upsert_relations(self, edges: Sequence[GraphEdge]) -> WriteReceipt: ...
```

### 6.2 VectorStorage

VectorStorage 使用通用 `VectorRecord`、`VectorQuery`、`VectorMatch`。Chunk、Entity 和 Relationship 的语义由 Indexer 负责投影，Storage 不接收业务实体。

不同 collection 通过不同 Storage 实例绑定：

```python
chunk_vectors = QdrantVectorStorage(client, collection="rag_chunks")
entity_vectors = QdrantVectorStorage(client, collection="rag_entities")
relation_vectors = QdrantVectorStorage(client, collection="rag_relations")
```

Record 不携带 namespace。resource、content revision 和 plugin projection metadata 只是可过滤字段。

### 6.3 GraphStorage 与 Chunk 图事实

GraphStorage 直接保存/读取统一的 `GraphNode`、`GraphEdge`，提供节点、关系、邻接和有界遍历。Neo4j 是从持久化 Chunk metadata 重建的拓扑投影，不包含 ACL、active revision、Ontology 解释、local/global/hybrid 或 Cypher 合同。

Mongo Chunk 事实保存 GraphExtractor 输出的 `metadata["entities"]` / `metadata["relationships"]`，是图投影的重建输入。GraphIndexer 从事实所属 Chunk 派生来源关联并写入 Neo4j 图投影；GraphRetriever 再从投影取得 Chunk ID，从 Mongo 批量读取 Chunk 正文并执行 revision/ACL 校验。Entity/Relationship vectors 与 Neo4j topology 均为可重建投影。

旧 `GraphFactStore` 的职责是保存抽取事实及其 Chunk 来源映射；现在抽取事实随 Chunk 持久化，来源关联进入 Neo4j 查询投影，因此再加一份仓储只会重复维护事实和增加写入/重建路径，不再保留独立 GraphFactStore 或 GraphChunkSource repository。

### 6.4 MetadataFilters

Plugin 负责：

- 哪些 Document metadata 可以进入 projection metadata；
- 哪些请求过滤字段对外开放；
- 请求字段如何映射为通用 filter key；
- 请求值和类型校验。

Storage 只执行通用 `MetadataFilters`，不解释 plugin、ACL、active revision 或 graph mode。

## 7. Retriever

### 7.1 Chunk 混合检索

普通 Chunk 检索独立于图谱能力。复用 `common.utils.retrieval` 的通用候选模型和组合能力，但保留 RAG 自己的 Qdrant 查询、版本/ACL 检查与 Chunk 回查：

```text
query + request scope
  -> RAG Qdrant Dense/Sparse retrievers
  -> Common Candidate + explicit Fusion
  -> RAG selected-revision/ACL validation
  -> Mongo Chunk batch lookup and authorized text hydration
  -> Common RetrievalPipeline rerank/diversity
  -> RankedCandidate -> RAG ChunkHit
```

Qdrant 候选先只携带 Chunk ID、resource/revision 等过滤 metadata；必须通过本地 ACL 和 selected revision 检查并从 Mongo 读到正文后，才构造带正文的 `Candidate` 交给 reranker。这样未授权或过期正文不会进入外部重排模型。

复用边界：

- 直接使用 Common 的 `Candidate`、`RankedCandidate`、`FusionRetriever` 和显式 `RoundRobinFusion`/`RrfFusion`；Dense 与 Qdrant sparse 查询由 RAG 实现为 Retriever adapter。
- RAG 的 request scope 通过请求级 Retriever 组合绑定，不写入共享 Retriever 实例的可变状态。
- `RetrievalPipeline` 可负责授权候选之后的 rerank、diversity 和最终 `RankedCandidate` 转换；RAG 再按 candidate ID/metadata 映射为 `ChunkHit`。
- Common `BM25Retriever` 使用调用方传入的内存 Candidate 列表并在本地建 BM25 索引，不等同于 Qdrant sparse 查询；不把它当成 Qdrant adapter。
- Fusion 必须显式选择算法；不依赖基类默认行为。
- 复用当前 `RetrievalPipeline` 会继承 reranker 失败时记录日志并保留原候选顺序的行为。P4 验收确认此降级语义；若业务要求 reranker 失败直接失败，只复用 Candidate/Fusion 等通用组件，由 RAG 编排重排阶段。

图谱不可用、未启用或没有图节点来源时，普通 ChunkHit 仍然有效。

### 7.2 图谱检索

GraphRetriever 只对齐 LightRAG 的 `local`、`global`、`hybrid`。它组合 GraphStorage Reader、Chunk 正文回查、Ontology filter、selected revision、ACL 和有界遍历。

`mix` 是外层能力：由 Chunk hybrid retrieval 与 graph retrieval 组合，不下沉到 GraphStorage 或 GraphRetriever。

### 7.3 Chunk 种子

ChunkHit 可以附带可选的 Entity `GraphNodeReference` 列表。它们只是候选，不是已选择的 seed。模型选择 node ID 后，必须重新校验：

- resource_id；
- content_revision；
- Chunk metadata 中的 node/relationship ID 与来源 Chunk 关联；
- active revision；
- PermissionScope/ACL。

只有校验通过的 node ID 才能进入 bounded traversal。未被模型选择的候选不能自动遍历。候选补充失败时返回普通 ChunkHit，候选列表为空。

### 7.4 父块

父块展开由外层工具参数控制，默认关闭。它不改变 Chunk identity、Chunk 中的图谱来源事实、Storage 合同或普通检索排序。

## 8. Ontology、Producer 和 Plugin

### 8.1 Ontology 三层约束

1. Prompt renderer 告知允许的 Entity category、Relationship type、端点方向和字段格式；
2. 结构化输出校验保证类型、端点、名称、描述和长度合法；
3. GraphIndexer 写入前再次执行统一业务硬校验。

非法事实按事实粒度丢弃，合法事实继续写入。Ontology 不增加独立版本号。

### 8.2 两条 Producer 路径

确定性结构 Producer 使用 Chunk 携带的结构字段和必要 metadata，构建 Resource、组织、人员、栏目和文档层级等事实。LLM GraphExtractor 补充正文语义事实。两者形成相同的 Entity/Relationship 模型，并将来源 Chunk ID 写入 GraphStorage 投影。

两条路径只保留基础 ID 去重和 upsert，不设计权威优先级、事实冲突仲裁或复杂合并状态机。LLM 可以补充结构 Entity 的 description，但 description 不是节点成立的必要条件。Chunk metadata 保存每个来源的抽取结果；跨 Chunk 的节点归一由 GraphIndexer 在构建投影时确定性完成。

### 8.3 Plugin identity

Plugin 只增加：

```text
name
version
plugin_id = hash(canonical(name) + "\0" + canonical(version))
```

PluginRegistry 负责空值、重复注册和名称冲突判定。不为 ontology、producer、filter 或 extractor 增加独立 ID。

## 9. 包结构和依赖方向

建议新实现目录如下：

```text
src/rag_v4/
  models/
  acl/
  publication/
  ingestion/       # Document, Chunker, IngestionPipeline
  contextual/      # Contextualizer
  indexing/        # VectorIndexer, GraphIndexer
  storage/         # ports, VectorStorage, GraphStorage
  retrieval/       # Chunk Retriever, GraphRetriever, seed validation
  graph/            # LightRAG core, Ontology, PluginRegistry
  adapters/         # Mongo, Qdrant, Neo4j, LLM, embedding
```

依赖方向：

```text
models <- ingestion/contextual/indexing/retrieval/graph
storage ports <- indexing/retrieval
adapters -> storage ports
adapters -> graph/acl/publication ports
composition -> all concrete implementations
```

Transformer 不依赖 Storage；Storage 不依赖业务策略；Retriever 不依赖 Indexer；HTTP/MCP 未来只依赖 application/composition 入口。

## 10. 一致性、失败和可观测性

### 10.1 接受的工程权衡

- ACL 本地投影允许短暂最终一致；RAG 不补偿上游重算完成语义；
- 多后端不存在统一瞬时观察点；
- 请求内固定开始时的 active revision，不追求请求结束时最新；
- 单个图谱投影失败不回滚已经完成的普通投影；
- 普通检索、图谱查询和直接读取分别决定局部失败和部分返回。

### 10.2 必须消除的错误

- 同一请求混用不同 content revision 的正文、Chunk、Entity 或 Relationship；
- 任何检索路径绕过统一 ACL；
- 未验证 source Chunk ID、resource 和 revision 的 node 进入图遍历；
- VectorIndexer、GraphIndexer、Transformer 各自解释 active revision 或 graph_enabled，导致语义漂移；
- GraphExtractor 产生的 metadata 被静默丢弃；
- Storage 直接理解业务检索 mode 或权限策略。

### 10.3 最小观测

只记录真实可解释的状态：

- ACL 刷新成功/失败、消费延迟和本地缺失；
- staged/applied 更新成功/冲突；
- Pipeline 阶段名称、成功/失败和耗时；
- Indexer 写入数量、失败和部分完成；
- graph fact 被 Ontology 拒绝的数量和原因；
- retrieval 候选被 revision/ACL 丢弃的数量。

不把这些指标命名为上游 ACL 版本、跨库 snapshot 或全局一致性证明。

## 11. 实现完成定义

RAG V4 核心实现完成，需要同时满足：

1. Chunk-only ingestion pipeline 可独立运行；
2. Contextual、VectorIndexer、普通 Chunk hybrid retrieval 可独立运行；
3. Chunk 中持久化的图谱 metadata 可以在不调用 LLM 的情况下重建 GraphStorage 和图向量投影；
4. GraphRetriever 的 local/global/hybrid golden cases 通过；
5. Chunk seed 到 bounded traversal 的 ACL/revision 校验通过；
6. ACL、ContentRevision、staged/applied 和删除语义通过故障矩阵；
7. 所有核心接口只依赖已确认的最小合同；
8. 无 HTTP/MCP route 也可以通过 application、fake adapter、真实 storage adapter 和离线重建测试。

## 12. 明确不做

- HTTP API、MCP tool schema 和 transport 注册；
- 上游 ACL 完成事件、上游版本协议或实时权威判权；
- 跨后端事务、全局 Snapshot、Fence、ACL revision；
- Chunk metadata Pydantic wrapper、selector、codec 注册链；
- Pipeline rollback、continue-on-error、跨 Indexer 并行调度；
- LlamaIndex Node/NodeWithScore/Postprocessor 兼容层；
- LightRAG 完整 runtime、workspace 和 provider storage；
- 字符级精确回源、公共 Range、Evidence resolver 和第二套证据账本。
