# RAG V4 子方案 05：Chunk 与 Document 级图谱边界

## 0. 已确认需求

本方案根据以下要求设计：

1. 混合 Chunk 检索可以脱离图谱独立运行；
2. Chunk 检索结果可以作为图谱遍历的种子入口；
3. RAG 统一使用 `Chunk` 命名，不再使用 `DocChunk`；
4. contextual context 作为 `Chunk.metadata` 的可选约定键，并通过 property 组装检索文本；
5. 保留 Document level metadata 作为垂类扩展；
6. 删除任意垂类 Chunk metadata 扩展机制和 `chunk_selector`，保留普通 pipeline metadata 作为阶段间传递载体；
7. 是否处理一个文档的图谱能力由 Document level 开关决定；
8. 可以跳过整个 Document 的图谱处理，但不能从一个已启用图谱的 Document 中任意跳过 Chunk。

## 1. 基本边界

### 1.1 Chunk 是唯一文本基本单元

`Chunk` 同时服务于：

- Dense/BM25 混合检索；
- contextual context 的目标文本；
- LLM 图谱抽取的输入文本；
- Chunk 到图节点的种子关联。

Chunk 不知道 Qdrant、Neo4j、ACL、插件类型或图谱查询策略。它只表达一份内容 revision 中的稳定文本事实。

### 1.2 混合检索独立可用

普通检索的最小闭环为：

```text
query
  -> dense candidates + BM25 candidates
  -> union/fusion/rerank
  -> Mongo Chunk 回查
  -> selected content revision + local ACL 判权
  -> ChunkHit
```

图谱不可用、未启用或某个 Document 没有图谱投影时，不影响该闭环返回可见 Chunk。

### 1.3 Chunk 作为图谱种子入口

Chunk 检索结果需要暴露与该 Chunk 关联的候选图节点，但不自动替模型决定遍历入口。结果至少携带稳定的 Chunk 身份：

```text
(resource_id, content_revision, chunk_id)
```

图谱能力从 Chunk 的持久化 `entities` metadata 取得该 Chunk 关联的图节点，并将同一 resource/content revision 下的节点摘要解析为
`GraphNodeReference`，附加到 `ChunkHit` 或其父级结果中。模型从这些候选节点中选择部分 node ID，再调用图谱遍历；RAG 不得默认遍历该
Chunk 暴露的全部节点。

这样可以保证：

- Chunk 检索不依赖图谱成功；
- 图谱查询不把 Chunk metadata 或检索器内部字段当成图事实；
- 模型只能从当前结果暴露的节点候选中选择入口；
- 被选中的 seed node 仍必须能通过持久化 Chunk metadata 确认属于同一 resource/content revision，并在图谱查询时再次经过 ACL
  与 revision 校验。

## 2. `Chunk` 模型

### 2.1 基础字段

新模型统一命名为 `Chunk`，至少包含：

```text
chunk_id
resource_id
content_revision
chunk_index
section_id
section_path
raw_text
metadata
content_token_count
anchor_labels
```

`chunk_id` 由 `resource_id + content_revision + Common chunk identity` 派生。它是 RAG 本地副本身份，不是上游字符定位或外部引用合同。

持久化的基础 `Chunk` 不保存完整 `GraphNode` 对象，但图谱启用时会把普通 `dict` metadata 中的 Entity/Relationship
抽取事实一并持久化。来源关系由事实所在 Chunk 隐含表达，不另存 `GraphChunkSource` 集合。检索侧直接从授权 Chunk metadata 生成轻量
`GraphNodeReference`，向模型暴露可选择的节点候选。

因此需要区分持久化 Chunk 和检索结果：基础 Chunk 保持内容事实稳定，`ChunkHit` 增加可选的 `graph_nodes`：

```python
@dataclass(frozen=True, slots=True)
class ChunkHit:
    # Chunk identity and retrieval scores omitted here.
    graph_nodes: list[GraphNodeReference] = field(default_factory=list)
```

`graph_nodes` 是当前 Chunk 暴露给模型的候选集合，不是已经选定的 traversal seed；模型选中的 node ID 只进入后续
`GraphRetrieveRequest.seed_node_ids`。

### 2.2 contextual context

contextual context 是 Chunk metadata 中的可选派生值，不进入 Chunk 的原始正文身份：

```python
@dataclass(slots=True)
class Chunk:
    raw_text: str
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = str(self.metadata.get("contextual_context") or "").strip()
        return f"{context}\n\n{self.raw_text}" if context else self.raw_text
```

约束如下：

- `raw_text` 始终是 Chunk 的正文事实；
- `metadata["contextual_context"]` 可以缺失、重建或由独立 contextual capability 生成；
- Dense、BM25 和 embedding 使用 `retrieval_text`；
- 图谱抽取默认使用目标 Chunk 的 `raw_text`，context 只作为明确允许的背景输入，不能把背景文本归因给目标 Chunk；
- contextual context 不改变 `chunk_id`、`content_revision` 或 Chunk 顺序。

### 2.3 Chunk 派生 metadata

删除的是任意垂类 Chunk metadata 扩展机制，而不是 pipeline 的派生结果载体：

- 删除 `DocChunkMetadata`、`GeneralChunkMetadata` 和对应 codec；
- 删除 `ChunkMetadataBuilder` 与 `RagPlugin.chunk_metadata_builder`；
- 保留由 Transformer 产生、由 Indexer 消费的普通派生 metadata；
- `metadata["contextual_context"]`、`metadata["entities"]` 和 `metadata["relationships"]` 是开发者约定键，不引入 Pydantic
  schema 或运行时注册表；
- `entities`、`relationships` 和 contextual 辅助信息可以暂时归属于来源 Chunk，但不改变它们在图谱中的独立语义。

普通 Chunk 摄入不依赖垂类 Chunk metadata 注册表；GraphExtractor 和 GraphIndexer 通过明确的 pipeline 合同传递这些结果。

## 3. Document metadata 与 Document 级开关

### 3.1 Document metadata

保留现有强类型 `DocumentMetadata` 和 `document_type` 路由：

- Document metadata 由上游或文档准备阶段确定性提供；
- metadata codec 只负责 Document 的序列化和恢复；
- plugin 可以根据 Document metadata 选择 ontology、deterministic producer、过滤字段和图谱规则；
- plugin 不再获得 Chunk selector，也不能改变 Chunk 集合。

### 3.2 `graph_enabled`

在 `Document`/`DocumentRevisionEntity` 增加明确的 Document 级 `graph_enabled: bool`，默认值由文档准备入口决定，建议默认为
`True`，由全局图谱开关作为能力级总闸门。

它不是 Chunk metadata，也不参与 Chunk ID 计算。它属于该 Document revision 的处理策略，和正文 revision 一起持久化，避免构建过程中改变语义。

语义如下：

| `graph_enabled` | Chunk/普通检索           | 图谱处理                                   |
|-----------------|--------------------------|--------------------------------------------|
| `True`          | 正常分块、写入和混合检索 | 该 revision 的全部 Chunk 进入图谱 pipeline |
| `False`         | 正常分块、写入和混合检索 | 跳过该 revision 的图谱抽取和图谱投影       |

`graph_enabled=False` 不得导致 Chunk 被删除、过滤出普通索引或无法作为普通检索结果返回。它只阻止本次 revision 的新图谱抽取；若该
revision 已有持久化图谱 metadata 和投影，则不因重复处理而清理它们。

### 3.3 Document 级开关的生产和消费

- `DocumentPreparer` 接收或解析 `graph_enabled`，并将它写入 Document revision；
- `VectorIndexer` 不读取该开关，保证普通 Chunk 索引独立；
- 组装入口根据该开关选择是否把 `GraphExtractor` 和 `GraphIndexer` 放入 Pipeline；组件自身不判断该开关；
- `graph_enabled=False` 时不配置图谱组件，不产生 Pipeline 错误，也不执行旧图谱清理；
- `graph_enabled=True` 时，组装入口把 GraphExtractor 和 GraphIndexer 放入 Pipeline；它们通过最小合同接收该 revision 的完整
  Chunk 列表。

插件 metadata 可以决定某个 Document 使用哪个垂类 graph producer，但不能通过返回 Chunk 子集改变输入范围。

## 4. 图谱事实与来源

图谱启用时，`metadata["entities"]` 和 `metadata["relationships"]` 随最终 Chunk 持久化；Chunk 的
`chunk_id/resource_id/content_revision` 就是事实来源，不再建立独立 GraphFactStore 或 GraphChunkSource 集合。

Chunk metadata 使用稳定 node/edge ID，使 GraphIndexer 可以从 Chunk 重建实体、关系及 source Chunk ID。Neo4j 拓扑投影随
GraphNode/GraphEdge 保存 source Chunk ID；GraphRetriever 从 Neo4j 取得来源 ID，再按 resource/revision 批量读取 Mongo Chunk
并做最终 ACL 校验。来源仍是粗粒度 Chunk 级，不增加字符 offset 或精确证据模型。

重建某个 revision 时，从 Mongo Chunk metadata 重放图投影，不重新调用 LLM；没有图谱 metadata 的 Chunk 仍能独立参与普通检索。

## 5. 两条独立查询路径

### 5.1 Chunk 混合检索

检索路径只依赖：

- Chunk vector storage；
- Mongo Chunk/Document 回查；
- selected content revision；
- local ACL capability；
- Common Candidate/Fusion/rerank。

GraphStorage 和 ontology 不属于普通 Chunk 检索的必需依赖。图节点候选直接从已授权 Chunk metadata 组装；组装失败时仍返回
ChunkHit，只将该结果的可选 node 候选置空并记录可观测原因。

### 5.2 Chunk 驱动的图谱查询

图谱入口接收 Chunk 检索结果及模型选出的 node ID：

```text
ChunkHit
  -> exposed GraphNodeReference candidates
  -> model selects a subset of node IDs
  -> validate selected IDs against resource/content revision/ACL
  -> bounded graph traversal
  -> selected revision + local ACL filtering
  -> graph result
```

图谱入口也可以接收显式 graph node ID，但由 Chunk 驱动的模型路径必须先经过候选暴露和范围校验。未被模型选中的暴露节点不得自动加入
traversal；没有节点候选的 Chunk 仍可作为普通检索结果，但不能生成图谱 seed。Chunk seed 不改变普通检索的排序结果，也不要求
Chunk 检索等待图谱。

## 6. 实施阶段

### 05-A：统一模型命名和 Chunk 基础事实

将 `DocChunk` 重命名为 `Chunk`，移除旧的任意 Chunk metadata codec/builder，保留普通 `dict` metadata、基础身份、正文和结构字段。

### 05-B：接入 contextual context property

约定 `metadata["contextual_context"]` 和 `retrieval_text` property；确保 raw text、chunk ID 和 graph target 语义不被
contextual 前缀改变。

### 05-C：Document metadata 与 graph_enabled

保留 Document metadata codec，增加 revision 级 `graph_enabled`，删除 plugin 的 `chunk_selector`，让
GraphExtractor/GraphIndexer 对启用文档消费全部 Chunk。

### 05-D：Chunk 图事实与种子入口

将 Chunk 派生的 Entity/Relationship metadata 交给 GraphIndexer 装配；Chunk facts 作为来源事实，Neo4j GraphStorage
投影保留图查询所需的来源 Chunk 关联。Chunk 混合检索独立运行，并在 ChunkHit/父级结果中暴露轻量 `GraphNodeReference`
候选。由模型选择部分 node ID 后，再通过 bounded graph traversal 执行图谱查询。

## 7. 验收证据

1. 普通文档没有任何插件 metadata 时仍能完成 Chunk 混合检索；
2. `graph_enabled=False` 时，所有 Chunk 仍进入普通索引，图谱构建被明确跳过；
3. `graph_enabled=True` 时，图谱输入包含该 revision 的全部 Chunk，没有 selector 跳过其中一部分；
4. contextual context 缺失时，`retrieval_text == raw_text`；存在时只影响检索文本，不改变 Chunk 身份；
5. 任意 Chunk metadata 扩展、Chunk selector 和相关 codec/builder 不再进入新架构；pipeline 仍可通过普通 metadata 传递
   contextual、Entity 和 Relationship 结果；
6. Chunk 检索在图谱服务不可用时仍返回正常 ChunkHit；
7. ChunkHit 可以暴露同 resource/content revision 的 `GraphNodeReference` 候选；
8. 模型只选择部分候选 node ID 时，图谱 traversal 只使用被选中的节点，不自动遍历其余候选；
9. 没有图谱 metadata 的 Chunk 仍是有效普通检索结果，且暴露的 node 候选为空；
10. 图节点候选补充失败仍可返回普通 ChunkHit；摄入中的 graph projection 失败停止 Pipeline，不回滚已完成写入，部分索引是否可见由外部
    revision 发布决定；
11. 同一 Chunk 暴露或选中的 node 不会跨 resource 或 content revision 连接到图节点。
