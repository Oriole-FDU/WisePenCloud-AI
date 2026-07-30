# RAG 仓储与实体职责

这份文档只解释 RAG 代码里“谁保存什么、谁读取什么”。实现细节在对应的协议和后端文件中，不在这里复制调用代码。

## 一、数据关系

```text
Kafka 正文事件
    |
    v
RagContentProjection（进程内投影）
    |
    +--> Mongo revision / parts / sections / reading_blocks / source_refs
    |          |
    |          +--> SourceRef + Markdown 还原图抽取 chunk
    |          +--> Section / ReadingBlock 支撑 Agent 阅读
    |
    +--> Qdrant RetrievalChunk（dense + Qdrant native BM25）
                   |
                   +--> candidate -> SourceRef / ReadingBlock 回源

ACL 事件 --> Mongo ACL projection --> Qdrant / Neo4j ACL payload

SourceRef 命中 --> Neo4j MENTIONS --> 跨文档 KnowledgeRelation
```

`RagRetrievalChunk` 是投影和 Qdrant 的检索对象，不是 Mongo 实体。Mongo 不再保存一份相同内容的 retrieval-chunk 副本；图抽取按
`SourceRef` 的原文 span 顺序重建同一批最小 chunk。这样图抽取仍能得到 `chunk_id / section_path / raw_text / source_spans`
，但不会产生第三份正文副本。

## 二、Mongo 实体

文件：`domain/entities/rag_content.py`

| 实体                                | 保存内容                                                               | 使用方                                                                           |
|-----------------------------------|--------------------------------------------------------------------|-------------------------------------------------------------------------------|
| `RagContentRevisionDocument`      | 一个投影 revision 的 `resource_id`、`document_version`、全文 `content_hash` | checkpoint 选择版本；读取时校验正文完整性                                                    |
| `RagContentPartDocument`          | revision 对应 Markdown 的连续大分片及全局 offset                              | SourceRef 回源、图抽取恢复全文；分片只为 Mongo 单文档大小服务                                       |
| `RagSectionDocument`              | 标题树节点：父子关系、ordinal、标题路径、摘要、`own/subtree` 范围                        | Section 导航和 frontier 返回。资源版本字段有意保留，因为它们是 Section 返回对象的真实字段                    |
| `RagSectionReadingBlockDocument`  | 一个 Section 内的有界正文块、原文 spans、页码/锚点标签                                | 命中后按 Section 回读完整正文；不保存无法回读的 transient locator/hash                           |
| `RagSourceRefDocument`            | `ref_id`、chunk/section 关系、精确 source spans、页码/锚点和资源版本               | Qdrant candidate 回源、evidence、图抽取 chunk 重建。每个 retrieval chunk 当前对应一个 SourceRef |
| `RagProjectionCheckpointDocument` | 当前 staged/applied 的 `content_revision` 和 `document_version`        | Kafka 重试、版本幂等、正文投影切换                                                          |

`RagSourceSpanDocument` 是上述实体内嵌的 offset 值对象，不是独立集合。

ACL 使用独立的 `RagAclProjectionDocument`（`domain/entities/rag_acl.py`）：它保存上游 ACL 事件在 RAG 侧的预计算结果，包括
revision、显式用户和组 ACL。它不保存正文，也不替代上游权威 ACL。

### 进程内投影对象

文件：`application/rag/ingestion/models.py`

| 对象                       | 作用                                        | 生命周期                               |
|--------------------------|-------------------------------------------|------------------------------------|
| `RagDocumentContent`     | Kafka 正文事件的最小输入                           | consumer 调用 projector 时存在          |
| `RagSectionNode`         | 标题树节点和 Section 范围                         | projector、Mongo、Section navigation |
| `RagSectionReadingBlock` | Section 内可回读的有界正文                         | projector、Mongo、evidence/tool      |
| `RagRetrievalChunk`      | embedding、Qdrant native BM25、rerank 的检索输入 | contextualize 到 Qdrant 写入；不落 Mongo |
| `RagSourceRef`           | chunk 到 Kafka Markdown 的稳定证据指针            | Qdrant payload、Mongo 回源、图 evidence |
| `RagContentProjection`   | 上述对象的同一 revision 组合                       | ingestion 编排的中间值                   |

`index_text` 只用于检索；`raw_text` 和 `source_spans` 才是证据与图抽取的正文基础。标题路径和 Section summary 被写入
`index_text`，不改变原文坐标。

## 三、仓储协议

协议集中在 `application/rag/repositories/`，按调用能力划分，而不是按数据库划分。

### `projections.py`

| 协议                                   | 能力边界                                               |
|--------------------------------------|----------------------------------------------------|
| `RagContentProjectionRepository`     | stage/apply checkpoint、读取 applied revision、提供图抽取输入 |
| `RagAclProjectionRepository`         | 读取/幂等更新本地 ACL projection，并提供上游 ACL 读取入口            |
| `RagAclProjectionTarget`             | 将 ACL projection 写入具体下游索引（Qdrant、Neo4j）            |
| `KnowledgeGraphProjectionRepository` | 初始化图约束、按正文 revision 失效/提交图、同步图 ACL                 |

### `retrieval.py`

| 协议                               | 能力边界                                                            |
|----------------------------------|-----------------------------------------------------------------|
| `RagVectorIndexRepository`       | Qdrant dense 向量复用、staged upsert、旧 revision 清理                   |
| `RagCandidateRepository`         | 按查询和权限范围从 Qdrant 取得候选                                           |
| `RagContextIndexingCache`        | 缓存 chunk 的 contextual indexing 文本                               |
| `RagSourceRepository`            | 从当前 applied revision 回读 SourceRef 和 ReadingBlock                |
| `RagSectionNavigationRepository` | 读取 Section 及 parent/sibling/children frontier、Section 全部 blocks |

### `navigation.py`

| 协议                                   | 能力边界                                    |
|--------------------------------------|-----------------------------------------|
| `KnowledgeGraphExtractionCache`      | 缓存 LLM/SDK 的窗口候选图，不缓存最终 revision 投影     |
| `KnowledgeGraphNavigationRepository` | Neo4j 中的 mention 解析和有界关系扩展              |
| `KnowledgeNavigationStateRepository` | Redis 保存导航会话的 user/session/query/已知节点集合 |

## 四、具体后端

| 实现                                        | 实现的协议                                        | 关键职责                                                                                 |
|-------------------------------------------|----------------------------------------------|--------------------------------------------------------------------------------------|
| `MongoRagContentProjectionRepository`     | content、source、section navigation 三组协议       | 维护 Mongo revision 及正文结构；同一个物理仓储通过窄协议暴露不同读取能力                                         |
| `MongoRagAclProjectionRepository`         | ACL projection                               | 保存 ACL 派生投影，按 ACL revision 防止旧事件覆盖新事件                                                |
| `QdrantRagVectorIndexRepository`          | vector index、ACL target                      | 写入 dense 向量和 Qdrant server-side BM25 sparse vector，payload 带 revision/ACL/source ref |
| `QdrantRagCandidateRepository`            | candidate repository                         | 执行混合召回并返回候选 payload；不负责正文回源                                                          |
| `Neo4jKnowledgeGraphRepository`           | graph projection、graph navigation、ACL target | 写入实体/关系/MENTIONS 和资源 revision；按权限做 mention 解析与多跳扩展                                   |
| `RedisRagContextIndexingCache`            | context cache                                | 缓存 contextual indexing 结果，key 包含 prompt/model/输入指纹                                   |
| `RedisKnowledgeGraphExtractionCache`      | graph extraction cache                       | 缓存 SDK 候选图 JSON，最终仍经过 mapper/projector                                               |
| `RedisKnowledgeNavigationStateRepository` | navigation state                             | Redis hash 保存会话元数据，set 保存去重后的 known node IDs，统一 TTL                                  |

物理实现可以实现多个窄协议，但业务服务只依赖它当前需要的协议；这避免把 Mongo、Qdrant、Neo4j 细节泄露到 tool 或 RAG 编排层。

## 五、版本与回源不变量

1. checkpoint 先选择唯一 applied revision，所有正文读取都必须带该 revision。
2. Mongo SourceRef、Section、ReadingBlock 与 revision 同步写入；Qdrant payload 和 Neo4j projection 使用同一个
   `content_revision`。
3. Qdrant 命中只提供候选和定位字段，正文必须通过 Mongo 的 SourceRef/ReadingBlock 回源。
4. 图抽取窗口只使用 SourceRef 对应的 Kafka Markdown；LLM 返回的引文经过 mapper 后才成为 KnowledgeEvidence。
5. ACL 是预计算投影：Qdrant 先过滤，Neo4j 导航再次按 ACL 谓词过滤；正文仓储不自行推断上游权限。
