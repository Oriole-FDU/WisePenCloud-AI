# WisePen `knowledge_navigate` 实施计划

> 本文只规划，不修改业务代码。

## 0. 结论摘要

`knowledge_navigate` 应是 Agent 唯一可见的私有知识阅读入口，公开动作仅保留 `locate` 和 `expand`：

- `locate` 复用现有 ES、Qdrant 和 ranking 能力，从原始 query 定位可继续导航的结构节点或显式概念节点；
- `expand` 从调用方明确给出的节点出发，在 Neo4j 中执行深度不超过 2 的有界遍历，并按 root query、局部 focus、关系价值、路径连贯性和 novelty 排序；
- 图只组织阅读路径，不成为最终事实来源。每个可引用节点和语义关系必须能回到原始 `SourceRef`；
- 长原文继续由 `ToolContentStore` 及现有读取工具处理，第一版不增加 `open` action；
- MVP 先建立可靠的文档拓扑，不引入 PPR、Community、全量 LLM 图抽取或完整 Claim 本体；
- `rag_knowledge_search` 的内部检索能力继续复用，但模型侧不能与 `knowledge_navigate` 同时看到两个重叠入口。

正式实施前必须先解决三个现有数据一致性风险：跨文档 `chunk_id` 可能碰撞、新版本不能原子替换旧版本、Neo4j builder 的 `clean_db=True` 不适合多文档增量图。否则稳定节点 ID、版本隔离和增量更新都无法成立。

## 1. 仓库调研结论

### 1.1 GitNexus

调研基线：提交 `bba25b2103f70fef53d0dc16a90e7479ca75046b`。

实际阅读的关键文件：

- `gitnexus/src/mcp/tools.ts`
- `gitnexus/src/mcp/server.ts`
- `gitnexus/src/mcp/resources.ts`
- `gitnexus/src/mcp/output-budget.ts`
- `gitnexus/src/mcp/local/local-backend.ts`
- `gitnexus/src/core/search/hybrid-search.ts`
- `gitnexus/src/core/ingestion/process-processor.ts`
- `gitnexus/src/core/ingestion/community-processor.ts`
- `gitnexus/src/lib/utils.ts`

核心调用链：

```text
query
  -> BM25 search + vector search
  -> reciprocal rank fusion
  -> matching symbols
  -> symbols 所属 Process 聚合
  -> definitions 与 process-oriented results 分组返回

context(uid/name)
  -> UID 或 name resolution
  -> 歧义候选
  -> incoming / outgoing references 分类
  -> Process membership

impact(uid, direction, relationTypes, maxDepth)
  -> 目标消歧
  -> 有界关系遍历
  -> 按 depth 分组、分页和 partial 标记
```

Process 不是在线临时生成：实现从入口 symbol 沿 `CALLS` 路径追踪，过滤短路径和低置信度路径，删除子集/重复端点，保留更完整路径，再写入 `STEP_IN_PROCESS`。Community 使用固定 seed 的 Leiden，在筛选后的代码关系投影上离线构建。因此两者的可解释性来自明确的代码语义和预计算，而不是任意图聚类。

可迁移设计：

- stable node ID 用于后续调用，名称只用于首次查找和歧义候选；
- `query -> context -> impact` 的连续阅读模式可压缩为 `locate -> expand`；
- incoming/outgoing、relation filter、max depth、结果上限和 partial/truncated 标记应进入统一返回协议；
- locate 结果应优先组织为阅读起点和路径，而非散乱 chunk Top-K；
- UID 解析失败或名称歧义时返回候选，不进行静默猜测。

不应照搬的设计：

- `generateId(label, name)` 实际只是字符串拼接，不足以满足跨资源、跨版本的全局稳定性；
- Process、Community 和代码调用边依赖代码语言的强结构语义，不能直接推广到通用文档；
- 多个公开 MCP 工具会让主模型在首次查询时提前选择错误入口；WisePen 应只暴露一个工具的两个 action；
- GitNexus 对最终字符串按估算 token/byte 硬截断，可能切断结构化输出。WisePen 应先按节点、边、路径预算裁剪，再序列化完整 JSON；
- source resource 应映射到现有 `ToolContentStore`，不在导航工具内返回大段原文。

对 WisePen 的映射：

```text
GitNexus query                -> knowledge_navigate.locate
GitNexus context              -> knowledge_navigate.expand(max_depth=1)
GitNexus impact               -> knowledge_navigate.expand(direction=..., max_depth=...)
GitNexus source               -> ToolContentStore + 现有内容读取工具
Process-oriented result       -> NavigationPath（会话态），不是 MVP 语料图节点
```

保留 `locate/expand` 足够覆盖 MVP。未来只有在评测证明 Agent 经常需要“在两个已知节点间找证据路径”时，才在同一工具内评估 `path` action；当前没有增加 `trace` 或第二个公开工具的必要。

### 1.2 Graphiti

调研基线：提交 `ca4d5e9d8c5d25d45917427b63daec17603a0d3a`。

实际阅读的关键文件：

- `graphiti_core/graphiti.py`
- `graphiti_core/nodes.py`
- `graphiti_core/edges.py`
- `graphiti_core/search/search.py`
- `graphiti_core/search/search_config.py`
- `graphiti_core/search/search_utils.py`
- `graphiti_core/search/search_filters.py`
- `mcp_server/src/graphiti_mcp_server.py`

核心调用链：

```text
search
  -> edge / node / episode / community 多对象并行召回
  -> BM25 / cosine / BFS 候选
  -> RRF 或 MMR / cross-encoder / node-distance rerank
  -> SearchResults

BFS search
  -> bfs_origin_node_uuids 作为遍历起点
  -> relation path + depth limit
  -> center_node_uuid 仅参与距离重排
```

Graphiti 的 `EpisodicNode` 保存原始内容、来源描述、时间和实体边，`EntityEdge` 保存 fact、来源 episode IDs、有效时间及可扩展属性。这个设计最值得迁移的部分不是动态记忆，而是“派生关系必须保留来源记录”。

具体结论：

- Episode 最接近 WisePen 的 `SourceRecord/SourceRef`，通常落到 Chunk 或 Span；它不应只映射成 `DocumentVersion`，因为后者粒度不足以证明一条关系；
- `EntityEdge` 适合表达带 evidence refs 的显式语义关系，但不适合作为所有结构关系的唯一模型，也不能代替具有条件、时间和立场的 Claim；
- `bfs_origin_node_uuids` 可直接映射到 `expand.node_ids`；`center_node_uuid` 应理解为排序中心，不应误当作遍历起点；
- 每条 Tier 2 关系必须保留 source refs、抽取器版本、置信度和限定条件；
- SearchConfig 适合作为内部实验配置的参考，不应把后端策略 DSL 暴露给主模型；
- `group_id` 是数据分区，不等于 WisePen ACL。WisePen 仍必须在 ES、Qdrant、Neo4j 各自执行同语义的权限谓词；
- 动态 episode 摄入、时间失效、LLM 实体抽取和 Community generation 面向 Agent memory，不适合静态文档 MVP。

Claim 的处理建议是两阶段：MVP 使用 evidence-backed relation record；当关系存在条件、适用范围、多来源、作者立场、时间或不确定性时，Phase 2 将其提升为独立 Claim 节点。这样既保留 Agent 看到的 `A -[DEPENDS_ON]-> B` 投影，也能进一步打开 Claim 查看限定条件和原文。

### 1.3 HippoRAG 2

调研基线：提交 `1e8f60981bf760b64003aa5bf5668126d0c106b3`。

实际阅读的关键文件：

- `src/hipporag/HippoRAG.py`

核心调用链：

```text
retrieve(query)
  -> get_fact_scores
  -> rerank_facts
  -> fact subject/object -> graph seeds
  -> graph_search_with_fact_entities
  -> dense passage weight + graph reset vector
  -> personalized PageRank
  -> passage nodes -> source documents
  -> 无有效 fact 时退回 dense passage retrieval

retrieve_ircot
  -> retrieval
  -> model thought
  -> 下一轮 retrieval
  -> 按 max score 合并 passage
```

实现中会按实体关联 chunk 数量分摊 seed 权重，缓解高频实体影响；同时把 dense passage 分数按 `passage_node_weight` 注入 PPR reset vector。它证明了“查询种子 + 图传播 + 原文映射”可行，也证明普通向量召回应当是图种子失败时的可靠 fallback。

对 WisePen 的结论：

- fact/entity seed 可作为未来 locate 的额外信号，但不能替代原始 query 的 ES/Qdrant 检索；
- PPR 可以作为 Phase 3 frontier ranking 实验，不能进入 MVP。即使做了度数归一，高连接 hub 仍可能放大，且传播分数会弱化显式路径解释；
- MVP 使用有界 BFS + 确定性 feature ranking，更容易执行 ACL、限制深度并返回完整路径；
- future rank 可融合 root relevance、local focus、relation prior、path coherence、novelty、depth/hub penalty，而不是直接复制 HippoRAG 最终 passage score；
- IRCoT 适合批量 QA，包含模型思考驱动的多轮召回，不适合作为一个低延迟、由 Agent 显式控制的导航工具内部循环；
- HippoRAG 返回最终文档排序，而 `knowledge_navigate` 必须返回增量节点、边、路径及来源引用，两者协议不能混用。

## 2. WisePen 现状定位

调研基线：原 RAG 实现在 `WisePenCloud-AI-new` 提交 `41fc51fb20374e44d45f2cff50aa94503d7ffd62`；当前 Python 工作区提交为 `fcc9f2c3179a1d915e8c424eb469023a1b3c053b`，Java `WisePenCloud` 提交为 `f482d6f96252f378482c2a7bb712526ff10637ed`。当前 Python 分支尚未包含 `application/rag`，所以实施第一步是把原 RAG 变更按当前分支架构落地并消除冲突，而不是另写一套平行检索。

### 2.1 关键模块和调用链

| 能力              | 文件                                                                                                            | 关键类型/职责                                                                                                          |
|-----------------|---------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------------------------------------|
| 公开 RAG 工具       | `application/tools/rag_tools/knowledge_search_tool.py`                                                        | `RagKnowledgeSearchTool`，当前要求模型传 `resource_id`，构造搜索请求                                                            |
| RAG 应用入口        | `application/rag/knowledge_search.py`                                                                         | `RagKnowledgeSearcher`，编排检索、gate、图增强和 context                                                                    |
| 检索编排            | `application/rag/retrieval/retrieval_pipeline.py`                                                             | `RagRetrievalPipeline`，ES prefilter -> embedding -> Qdrant -> ranking -> answerability -> parent materialization |
| ES 严格过滤         | `application/rag/retrieval/pipeline/elastic_filter.py`                                                        | `RagElasticFilter`，关键词 match phrase + scope + ACL                                                                |
| Dense/Sparse 召回 | `application/rag/retrieval/pipeline/qdrant_retrieve.py`                                                       | `RagQdrantRetriever`，dense 与 Qdrant BM25 并发，并保留 channel rank/score                                               |
| 排序              | `application/rag/retrieval/pipeline/ranking.py`                                                               | 现有 RRF-style ranking，保留原始 query                                                                                  |
| 权限              | `application/rag/retrieval/permission_filter.py`                                                              | `RagPermissionFilterBuilder`，生成 ES/Qdrant/Neo4j 同语义 VIEW predicate                                               |
| 图增强             | `application/rag/retrieval/pipeline/graph_enhancement.py`                                                     | 在 answerability warning 后以 direct evidence 为种子增强                                                                 |
| 图写入             | `application/rag/graph/graphrag_builder.py`                                                                   | neo4j-graphrag LLM 图抽取和 lexical graph                                                                            |
| Neo4j 访问        | `core/persistence/neo4j/rag_repository.py`                                                                    | 文档图删除、ACL 投影、warning expansion                                                                                   |
| Qdrant 访问       | `core/persistence/qdrant/rag_repository.py`                                                                   | 向量 payload 写入、删除和 ACL 更新                                                                                         |
| ES 访问           | `core/persistence/elasticsearch/rag_repository.py`                                                            | 检索文档写入、删除和 ACL 更新                                                                                                |
| 语料事实表           | `domain/entities/rag_corpus.py`                                                                               | Mongo parent/child chunks、offset 和 locators                                                                      |
| 语料访问            | `core/persistence/mongo/rag_corpus_repository.py`                                                             | parent/child chunk 保存和加载                                                                                         |
| 摄入编排            | `application/rag/ingestion/ingester.py`                                                                       | chunk/context index/embed 后顺序写 Mongo、Qdrant、ES、Neo4j、ACL                                                         |
| 内容事件            | `application/rag/kafka_consumers/document_ready_consumer.py`                                                  | `DocumentReady` 转换为 ingestion payload                                                                            |
| ACL 事件          | `application/rag/kafka_consumers/acl_recalculate_consumer.py`                                                 | 刷新各后端 ACL 投影                                                                                                     |
| 上游内容契约          | `WisePenCloud/wisepen-document-service/wisepen-document-api/.../DocumentReadyMessage.java`                    | Java 上游只发送 `resourceId/version/content`；`content` 是 RAG 唯一正文输入                                                     |
| 上游内容生产          | `WisePenCloud/wisepen-document-service/wisepen-document-biz/.../DocumentServiceImpl.java`                     | 文档处理完成后发布 `DocumentReadyMessage`；chat-service 不参与文档转换/解析                                                      |
| Resource 权威数据    | `WisePenCloud/wisepen-resource-service/wisepen-resource-biz/.../ResourceItemEntity.java`                      | `wisepen_resource_items` 是资源主档和 ACL 权威来源；本地 ACL 只允许做派生投影                                                        |
| Resource 权威鉴权    | `WisePenCloud/wisepen-resource-service/wisepen-resource-biz/.../ResourceServiceImpl.java`                     | `checkPermission` 从资源主档实时计算，不依赖预计算 ACL；现有 Python `ResourceClient.check_res_permission` 可调用该边界                    |
| Resource 生命周期事件 | `WisePenCloud/wisepen-resource-service/wisepen-resource-api/.../ResourceDeletedMessage.java`                  | 资源服务物理销毁后广播 `typedResourceIds`；chat-service 只能据事件清理自己的派生索引                                                   |
| Tool schema     | `application/tools/core/definition.py`                                                                        | Draft 2020-12 JSON Schema                                                                                        |
| preflight       | `application/tools/core/execution/hooks/base.py`、`hooks/builtin.py`、`executor.py`                             | 工具执行前 hook；仓库中没有可正确表达本动作契约的 `exactly_one_of`                                                                     |
| 长内容缓存           | `application/tools/common/tool_content_store/store.py` 及 `application/tools/session_tools/tool_content_read/` | `ToolContentStore` 生成 `cnt_*`、分块和 locator；现有 range/regex/ranked read 负责打开内容                                      |
| 排序基础设施          | `application/utils/ranking/`                                                                                  | `RankingPipeline`、`RankRequest`、Weighted RRF、reranker、MMR；原 RAG 的 `ranking_engine` 适配需迁移到当前接口                    |
| Redis 装配        | `container.py`、现有 Redis repositories                                                                          | 可复用 client/序列化/TTL 约定，新增独立 navigation state repository                                                           |

现有调用链为：

```text
RagKnowledgeSearchTool
  -> RagKnowledgeSearcher.search
  -> RagRetrievalPipeline.retrieve
     -> optional Elastic strict prefilter
     -> embed original query
     -> Qdrant dense + sparse
     -> ranking
     -> hard/soft answerability
     -> parent chunk materialization
     -> warning-triggered Neo4j enhancement
  -> context builder
```

`knowledge_navigate.locate` 只应复用其中“候选召回与排序”部分，不执行 answerability gate、自动图增强或回答 context 拼装。建议把这段能力收敛成一个复用服务 `RagCandidateRetriever`，由原 `RagRetrievalPipeline` 和新的 `KnowledgeLocator` 共同调用，避免复制 ES/Qdrant/ranking 逻辑。

### 2.2 真实内容、Resource 和解析边界

RAG 不调用 chat-service 的 `application/utils/document_parse`，也不接触原文件、MinerU、Docling、Office XML 或 HTML DOM。唯一正文入口是 Kafka `DocumentReadyMessage.content`：

```text
wisepen-document-service
  -> 完成转换与解析
  -> DocumentReadyMessage(resourceId, version, content)
  -> wisepen-document-ready-topic（key=resourceId）
  -> RagDocumentReadyConsumer
  -> 对 content 做 RAG chunk/index，不重新做文档解析
```

Java `DocumentServiceImpl.finalizeToReady` 从 `DocumentContentEntity` 选择 Markdown 或 raw text 后发布事件。`RagDocumentReadyConsumer` 只把三个消息字段映射为 `RagMarkdownIngestionPayload`。因此知识导航能使用的结构上限由 Kafka `content` 明确携带的标记决定，不能回头从 Python 文档解析器补数据。

Java 提交 `772377c39599f7dc8413d221d3b7deefe14868fc` 已把页标记改为：

```text
<!-- page:start page=1 -->
...
<!-- page:end page=1 -->
```

但 formal 分支 `application/utils/chunkers/markdown/parser.py` 的 `PAGE_MARKER_RE` 仍只识别 `<!-- page 1 -->`。RAG 虽然不负责 document parse，仍会对 Kafka 正文做 Markdown chunking；所以必须在该 chunker 中对齐新的消息内容格式，并让 page locator 按成对的 start/end 边界生成。不能把这个修复错误地描述为“接入 chat document parse”。

当前 `content` 可以可靠支持 heading、段落、Markdown table、明确图片语法、顺序、offset 和新页边界。它不能证明已经携带：

- 跨页语义续接；
- 脚注 reference 与 target 的结构绑定；
- citation mention 与 bibliography 条目的结构绑定；
- 原始 Office/HTML 中未出现在 `content` 的链接、图片、图表或对象关系。

MVP 只能从 Kafka 正文确定性构建 `CONTAINS/PARENT_OF/NEXT/PREVIOUS`，以及由正文明确编码的 table/caption/image/anchor 关系。`CONTINUES`、`FOOTNOTE_OF`、`CITES` 和 `HYPERLINKS_TO` 必须等待上游事件契约显式携带对应结构信号；chat-service 不自行重解析或猜测。

Resource 边界同样明确：`wisepen_resource_items` 是资源存在性、主档和 ACL 的唯一权威数据。RAG 自己的 Mongo/ES/Qdrant/Neo4j 记录全部是可重建的派生投影，不能反向创建、更新、软删或硬删 Resource，也不能把本地 ACL projection 当成最终授权结论。

### 2.3 必须先处理的现有风险

1. **ID 碰撞**：当前 chunk ID 形如 `role:index:content_hash`，Qdrant point ID 又只从 `chunk_id` 生成 UUID；相同位置和内容可能跨资源/版本碰撞。所有新投影必须使用 `resource_id + document_version + native_chunk_id` 组成存储键。
2. **旧版本可见**：摄入清理逻辑按传入的同一 document version 处理，检索只过滤 resource。新 `DocumentReadyMessage.version` 到达后旧派生版本可能继续被召回；需要本地 projection checkpoint 控制派生索引可见性，但它不是 Resource 或 Document 的权威 manifest。
3. **图清库风险**：`Neo4jWriter(clean_db=True)` 会破坏多资源增量语义，必须改为按 projection namespace upsert/delete。
4. **多存储部分成功**：Mongo、Qdrant、ES、Neo4j 顺序写入，没有派生投影的统一可见性提交点。需要 staged projection + applied checkpoint，而不是假设跨库事务或宣称拥有上游版本控制权。
5. **页标记契约漂移**：Java 已输出 `page:start/page:end`，Python chunker 仍识别旧 marker；不修复会直接丢失 page locator。
6. **权限上下文不完整**：原工具构造 `RagPermissionScope(group_role_map={})`，会漏掉组权限；旧 `RagAclProjectionProjector` 还是预过滤投影，不能替代 Java 的实时 `checkPermission`。
7. **软删除窗口**：resource-service 软删除时立即从 `wisepen_resource_items` 移除，但只在后续物理销毁时发布 `ResourceDeletedMessage`。本地索引可能暂时残留，返回前必须实时校验 Resource `VIEW`，不能只等删除事件。
8. **Neo4j seed 歧义**：当前图查询只按 `chunk_id` 找 seed；ID 修复前可能跨资源命中。所有 MATCH 必须使用复合 projection key 或稳定 node ID。

## 3. 最终架构

### 3.1 能力边界

`knowledge_navigate` 负责：

- 按原始 query 定位 canonical navigation nodes；
- 按指定 node、关系、方向和深度展开；
- 对候选 frontier 排序、去重并维护会话导航状态；
- 返回小预览、证据关系、路径和可被现有读取工具消费的 `content_ref`；
- 在每次 locate/expand 中执行权限和版本过滤。

它不负责：

- 生成最终答案；
- 在工具内部自动多轮思考或自动决定下一跳；
- 重写后丢弃原始 query；
- 打开、分页、regex 或 ranked read 长原文；
- 把 embedding 相似性写成事实关系；
- 对所有文档做完整 ontology 或无约束关系抽取。

### 3.2 模块结构

为贴合现有应用层/持久化层边界，采用下列结构，而不把 repository protocol 放进 tool 目录：

```text
chat/application/knowledge_navigation/
    models.py
    navigator.py
    locator.py
    traverser.py
    node_resolver.py
    frontier_ranker.py
    result_builder.py
    repository_protocols.py
    indexing/
        topology_builder.py
        projection_models.py

chat/application/tools/knowledge_navigation/
    knowledge_navigate_tool.py
    action_check.py

chat/core/persistence/neo4j/
    knowledge_navigation_repository.py

chat/core/persistence/redis/
    knowledge_navigation_state_repository.py
```

Phase 2 再增加 `indexing/semantic_relation_builder.py` 和 Claim 相关模型；MVP 不提前创建通用 extractor framework。

### 3.3 调用、状态和回源

```text
                              server-injected principal + scope fingerprint
                                               |
                                               v
Agent -> knowledge_navigate -> KnowledgeNavigateTool/action preflight
                                  |
                   +--------------+--------------+
                   |                             |
                locate                         expand
                   |                             |
                   v                             v
          KnowledgeLocator             NavigationStateRepository
          | ES exact/lexical               | load + bind + CAS revision
          | Qdrant dense/sparse            v
          | RankingPipeline          KnowledgeTraverser
          v                          | Neo4j bounded traversal
       NodeResolver                  | ACL on every path node
          |                          | version + relation filters
          +-------------+------------+
                        v
                 FrontierRanker
       root relevance + local relevance + edge/path/novelty penalties
                        |
                        v
                NavigationResultBuilder
              | hydrate SourceRef from Mongo
              | materialize parent text once
              v
                 ToolContentStore (cnt_*)
                        |
                        v
        nodes + edges + paths + content_ref + next relation counts

Later Agent read:
content_ref -> existing sequential / regex / ranked content read tools
```

`KnowledgeNavigateTool` 只做 schema/preflight、可信上下文转换和错误映射。`KnowledgeNavigator` 是单一应用编排入口。后端具体查询留在 repository；`RagPermissionFilterBuilder` 只负责基于派生 ACL 的候选前置过滤，最终授权统一交给现有 `ResourceClient.check_res_permission`，不能在 traverser 中复制 Java 权限算法。

### 3.4 权限边界

可信调用上下文提供 principal 和 opaque `knowledge_scope_fingerprint`。这些字段不进入公开 schema，也不能由模型覆盖。Resource 是否存在以及当前用户是否拥有 `VIEW`，以 resource-service 基于 `wisepen_resource_items` 实时计算的结果为准。

权限检查位置：

1. ingestion/ACL event：从 Resource 权威数据读取当前资源记录并生成本地 ACL projection；projection 只写入 ES/Qdrant/Neo4j，作为候选过滤缓存；
2. locate 召回：ES、Qdrant 和 Neo4j alias lookup 使用派生 ACL 前置过滤，减少无权候选；
3. locate 返回：按候选中的唯一 `resource_id`，有界并发调用 `ResourceClient.check_res_permission`，仅保留实时允许 `VIEW` 的节点和 source；
4. expand 遍历：起点、所有中间节点、终点和 edge 两端先应用 Neo4j 派生 ACL predicate；遍历完成后，再对路径涉及的每个唯一 Resource 做实时 `VIEW` 校验；
5. source：没有通过权威校验的 `SourceRef`、节点、边和整条路径全部丢弃，不能只隐藏 preview；
6. state：校验 user、session、scope/principal fingerprint、Kafka source version 和 graph projection revision；每次 expand 都重新校验当前 focus 和返回候选的 Resource 权限，不依赖不存在的本地 ACL revision；
7. relation counts：只在权威权限校验后统计可返回邻接，不能泄露已软删或不可读节点的名称、数量或存在性。

派生 ACL 过宽时，实时校验负责 fail closed；派生 ACL 过窄时可能暂时损失 recall，应通过 ACL event lag 指标发现并修复，不能绕过权威校验补偿。为控制 RPC 数量，每次调用先按 `resource_id` 去重，并受内部 resource-check cap 限制；超出 cap 时缩小候选批次，不能跳过校验。

权限或版本校验失败统一返回 `state_invalidated`，要求重新 locate；伪造 state ID、错误 user/session 与不存在 state 使用相同的 not-found 外观，避免 oracle。

## 4. 数据模型

### 4.1 固定值类型

固定集合使用 `StrEnum`，不散落自由字符串：

```python
class NavigationAction(StrEnum):
    LOCATE = "locate"
    EXPAND = "expand"

class NavigationDirection(StrEnum):
    IN = "in"
    OUT = "out"
    BOTH = "both"

class KnowledgeNodeType(StrEnum):
    DOCUMENT = "document"
    SECTION = "section"
    CHUNK = "chunk"
    TABLE = "table"
    FIGURE = "figure"
    CITATION = "citation"
    CONCEPT = "concept"

class RelationOrigin(StrEnum):
    DETERMINISTIC = "deterministic"
    EXPLICIT_TEXT = "explicit_text"
    QUERY_CANDIDATE = "query_candidate"
```

MVP `RelationType` 只注册实现中真实存在的关系。`CONTINUES/CITES/HYPERLINKS_TO/MENTIONS/DEFINED_IN` 可以先存在于模型，但只有相应 builder 有可靠输入时才产出；不能为了填满枚举伪造边。

### 4.2 SourceRef

```python
@dataclass(frozen=True, slots=True)
class SourceRef:
    ref_id: str
    resource_id: str
    document_version: str
    parent_chunk_id: str
    chunk_id: str | None
    content_ref: str
    source_start: int | None
    source_end: int | None
    content_start: int
    content_end: int
    page_label: str | None
    section_path: tuple[str, ...]
    anchor_labels: tuple[str, ...]
```

`source_start/end` 是归一化文档中的位置；`content_start/end` 是该 `content_ref` 内的位置。两套 offset 不能混为一套。Section 或 Claim 可以有多个 `SourceRef`。`content_ref` 直接使用现有 `ToolContentStore` 的 `cnt_*` ID，不新增 open 协议。

### 4.3 KnowledgeNode

```python
@dataclass(frozen=True, slots=True)
class KnowledgeNode:
    node_id: str
    node_type: KnowledgeNodeType
    label: str
    preview: str
    source_refs: tuple[SourceRef, ...]
    available_relations: Mapping[RelationType, int]
    properties: Mapping[str, JsonScalar]
```

`properties` 只能放有消费者的类型特有字段，例如 Section level、Citation key；不存任意 extractor dump。resource/version 从 `SourceRef` 得到，不在 node 顶层复制一份可能不一致的数据。公开 result 可为便捷消费投影 primary source 的 resource/version，但内部模型保持单一真相。

节点 ID 规则：

```text
kn_<base64url(sha256(namespace | resource | version | node_type | source_key))[:22]>
```

- Document/Section/Chunk/Table/Figure/Citation 使用资源、版本和确定性 source key；
- raw `chunk_id` 只能作为 source key 的一部分，不是全局 ID；
- 权威 glossary/external ID 的 Concept 使用 tenant/scope namespace + canonical key；
- 非权威概念默认文档内消歧：normalized label + definition span/resource discriminator；
- 同名概念不自动合并，alias lookup 可以返回多个带来源的候选。

### 4.4 KnowledgeEdge

```python
@dataclass(frozen=True, slots=True)
class KnowledgeEdge:
    edge_id: str
    source_node_id: str
    target_node_id: str
    relation_type: RelationType
    origin: RelationOrigin
    confidence: float
    evidence_ref_ids: tuple[str, ...]
    extractor_version: str | None
    qualifiers: Mapping[str, JsonScalar]
```

- Tier 1 edge 固定 `origin=deterministic`、`confidence=1.0`，结构来源由 projection revision 追踪；
- Tier 2 edge 必须有非空 evidence refs、extractor version 和经过校准的 confidence；
- Tier 3 只存在于查询候选/导航状态，不作为 Neo4j 正式事实；
- `direction` 是相对于本次 focus 的返回投影，不重复存入 edge 事实；
- qualifiers 只保存明确抽取的条件、时间、范围等，不允许模型补全缺失信息。

### 4.5 NavigationPath 和 Candidate

```python
@dataclass(frozen=True, slots=True)
class NavigationPath:
    node_ids: tuple[str, ...]
    edge_ids: tuple[str, ...]
    depth: int
    score: float

@dataclass(frozen=True, slots=True)
class NavigationCandidate:
    node: KnowledgeNode
    via_edge: KnowledgeEdge | None
    path: NavigationPath
    feature_values: Mapping[str, float]
    feature_ranks: Mapping[str, int]
```

路径保存完整 node/edge ID 序列，但最大深度为 2，且 state 只保留已返回路径，不保留所有 BFS 中间候选。feature values 是内部诊断数据，不默认暴露给模型；对 Weighted RRF，应先把各 feature 排成 rank signal，不能把未归一化数值直接当 RRF rank。

### 4.6 NavigationState

```python
@dataclass(frozen=True, slots=True)
class KnowledgeNavigationState:
    state_id: str
    revision: int
    user_id: str
    session_id: str
    root_query: str
    current_focus: str | None
    visited_node_ids: frozenset[str]
    visited_edge_ids: frozenset[str]
    paths: tuple[NavigationPath, ...]
    materialized_content_refs: Mapping[str, str]
    scope_fingerprint: str
    principal_fingerprint: str
    source_versions: Mapping[str, str]
    graph_schema_version: str
    graph_data_revision: str
    node_budget_remaining: int
    content_budget_remaining: int
    created_at: datetime
    expires_at: datetime
```

设计约束：

- 只存 Redis，opaque `kns_*` ID 至少 128 bit 随机熵；
- 固定 TTL 建议 30 分钟，不滑动续期；state TTL 必须小于等于 ToolContentStore 内容 TTL；
- 绑定 user/session/scope/principal，不允许跨会话复用；`source_versions` 只记录 Kafka 内容版本，不代表 Resource 主档版本；
- 不保存完整 frontier，expand 从明确 `node_ids` 重算，避免状态爆炸和陈旧授权候选；
- 默认上限：500 visited nodes、1000 visited edges、100 paths；超限返回 `budget_exhausted`；
- `materialized_content_refs` 记录 source parent key 到 `cnt_*` 的映射，避免同一 state 重复写缓存；
- 不保存 `opened_content_refs`，因为现有内容读取工具没有可靠的反向消费信号；
- Redis 使用 compare-and-set revision，防止同一 state 并发 expand 丢更新。

### 4.7 Tool Result

```json
{
  "state_id": "kns_xxx",
  "action": "expand",
  "root_query": "...",
  "focus": {"query": "...", "node_ids": ["kn_xxx"]},
  "nodes": [],
  "edges": [],
  "paths": [],
  "navigation": {
    "visited_nodes": 12,
    "frontier_nodes": 7,
    "truncated": false,
    "exhausted": false
  }
}
```

结果只返回本次增量。`frontier_nodes` 是本次查询中当前可授权候选的数量，不是 state 中持久化的 frontier。节点 preview 从原文截取，默认最多约 320 字符；不得用小模型摘要替换原文。

## 5. Tool Contract

### 5.1 公开 schema

保持一个工具和两个 action：

```python
knowledge_navigate(
    action: Literal["locate", "expand"] = "locate",
    query: str | None = None,
    state_id: str | None = None,
    node_ids: list[str] = [],
    relation_types: list[str] = [],
    direction: Literal["in", "out", "both"] = "both",
    max_depth: int = 1,
    max_results: int = 10,
)
```

实际 Pydantic/dataclass 使用 tuple 或 `default_factory=list`，不能使用可变默认值。scope、user、session、ACL、版本、candidate limit 和后端参数全部由系统注入，不进入公开参数。

边界值：

- `node_ids`: 去重后 1～16 个；
- `relation_types`: 去重后最多 16 个，值必须来自已注册 `RelationType`；
- `max_depth`: 1～2；
- `max_results`: 1～20；
- 所有字符串 trim 后校验，空字符串按缺失处理；
- additional properties 一律拒绝。

### 5.2 locate

| 参数 | 规则 |
|---|---|
| `action` | `locate` 或省略 |
| `query` | 必填、非空，原样成为 state `root_query` |
| `state_id` | 禁止 |
| `node_ids` | 必须为空 |
| `relation_types` | 必须为空 |
| `direction` | 禁止显式传入；不参与 locate |
| `max_depth` | 禁止显式传入；不参与 locate |
| `max_results` | 允许 |

执行成功创建新 state。零结果也返回有效但 `exhausted=true` 的 state，便于区分“请求非法”和“知识库无匹配”；是否保留空 state 可由 TTL 自动清理。

### 5.3 expand

| 参数 | 规则 |
|---|---|
| `action` | 必须为 `expand` |
| `state_id` | 必填 |
| `node_ids` | 必填，且每个节点必须已在该 state 中返回过 |
| `query` | 可选，只更新本轮 `current_focus`，永不覆盖 `root_query` |
| `relation_types` | 可选；为空表示节点类型对应的默认可导航关系集 |
| `direction` | 默认 `both` |
| `max_depth` | 默认 1，最大 2 |
| `max_results` | 允许 |

默认 direction 选择 `both`，因为文档结构边的存储方向对 Agent 不直观；排序时仍按关系语义区分方向价值。默认关系集合不应对所有节点开放宽泛 `MENTIONS`，Concept 节点显式请求或配置允许时才走该关系。

重复 expand 不返回已 visited 节点，除非它是连接新路径所必需的中间节点；这种中间节点只出现在 path，不重复计入 nodes 增量。所有候选已访问时返回 `exhausted=true`，不是错误。

### 5.4 校验实现

JSON Schema 负责类型、enum、长度、数量和 `additionalProperties=false`。动作间组合语义由工具本地 `KnowledgeNavigateActionCheck` 负责，并在错误中明确指出哪一字段在当前 action 下 required/forbidden。

不新增通用 `ToolActionContract/ActionParameterRule` DSL：目前只有一个工具需要该组合规则，引入通用抽象没有真实复用者。待第二个工具出现同构 action 契约后再抽取。也不使用 `exactly_one_of`，因为 locate/expand 同时包含 required、forbidden、conditional allowed 三类规则。

### 5.5 错误语义

| reason | retryable | 含义 |
|---|---:|---|
| `knowledge_navigate_invalid_request` | false | schema/action 参数错误 |
| `knowledge_navigation_state_not_found` | false | 不存在、伪造或绑定不匹配 |
| `knowledge_navigation_state_invalidated` | false | ACL/scope/graph/document revision 已变化，需重新 locate |
| `knowledge_navigation_budget_exhausted` | false | state 节点或内容预算耗尽 |
| `knowledge_navigation_content_unavailable` | true | ToolContentStore 写入失败，不能返回悬空 content_ref |
| `knowledge_navigation_backend_unavailable` | true | ES/Qdrant/Neo4j/Redis 暂时不可用 |

MVP 切换后，tool registry 只向模型暴露 `knowledge_navigate`。旧 `RagKnowledgeSearchTool` 的内部 service 可以保留给非 Agent consumer，但不能与新工具同时出现在同一 tool scope；回滚通过 feature flag 原子选择其中一个入口。

## 6. 索引构建方案

### 6.1 MVP 图投影

MVP 节点集合：

- `Document`：具体 document version 的根节点，不是跨版本抽象资源；
- `Section`：由 heading 层级和 section path 确定；
- `Chunk`：无法可靠提升为更具体节点时的检索/导航 fallback；
- `Table`、`Figure`：仅当 Kafka `content` 中存在明确 Markdown table/image block；当前 Java Office 路径未输出的图片/图表不得补造；
- `Citation`：仅当 Kafka `content` 自身提供可配对的 citation/link target；
- `Concept`：只来自标题、glossary、显式定义或已有可靠实体结果，不用高频词直接写正式概念。

MVP 确定性关系：

| 关系 | 产生条件 | origin/confidence |
|---|---|---|
| `CONTAINS` | Document/Section 包含直接子节点 | deterministic / 1.0 |
| `PARENT_OF` | heading 层级明确 | deterministic / 1.0 |
| `NEXT`、`PREVIOUS` | 同一父节点下的文档顺序 | deterministic / 1.0 |
| `CAPTION_OF` | Kafka 正文中的 caption 与紧邻 table/image block 可确定绑定 | deterministic / 1.0 |
| `TABLE_CONTAINS` | 表格结构保留了 header/cell 子节点时 | deterministic / 1.0 |
| `CONTINUES` | 未来 Kafka 契约明确输出跨页 continuation signal 时 | deterministic / 1.0 |
| `FOOTNOTE_OF` | 显式 footnote reference/target 可配对时 | deterministic / 1.0 |
| `HYPERLINKS_TO` | 显式 URI/anchor target 可解析时 | deterministic / 1.0 |
| `CITES` | citation mention 与 bibliography key 明确配对时 | deterministic / 1.0 |
| `DEFINED_IN` | glossary 或明确“X 是/指”定义规则命中并保留 span | explicit_text / calibrated |
| `MENTIONS` | 仅对受控概念词典或可靠已有实体结果 | explicit_text / calibrated |

`SAME_SECTION` 不必实际写边，可由共同 parent 推导；否则同章节节点数平方增长。`NEXT` 与 `PREVIOUS` 可以只存一种方向并在 repository 中投影反向，但公开协议仍返回明确 relation type；选择哪一种必须在 schema migration 中固定，避免双写重复。

Tier 2 关系 `DEFINES/EXPLAINS/DEPENDS_ON/REQUIRES/IMPLEMENTS/DERIVED_FROM/CONTRASTS_WITH/SUPERSEDES/CONTRADICTS` 延后到 Phase 2。仅当原文中存在明确关系触发、subject/object 可定位且 evidence span 非空时写入。抽取失败或低置信候选进入离线审计/查询候选，不进入正式图。

Tier 3 的 `SEMANTICALLY_RELATED/SHARED_ENTITY/POSSIBLE_BRIDGE/POSSIBLE_DEPENDENCY` 永不写成正式 Neo4j 事实。它们留在 Qdrant 相似候选或当次 navigation candidate 中，返回时也不得伪装成 evidence-backed edge。

### 6.2 Neo4j 物理模型

避免为每一种语义创建独立 repository 和大量动态 relationship type。建议物理模型为：

```text
(:KnowledgeNode {
  node_id, node_type, resource_id, document_version,
  projection_key, label, preview, source_ref_ids,
  graph_schema_version, graph_data_revision,
  owner_id, readable_users, computed_group_acls
})

(:KnowledgeNode)-[:KNOWLEDGE_RELATION {
  edge_id, relation_type, origin, confidence,
  evidence_ref_ids, extractor_version, qualifiers_json,
  graph_data_revision
}]->(:KnowledgeNode)
```

所有查询把 `relation_type` 作为受枚举约束的属性过滤，不把模型输入拼进 Cypher relationship syntax。结构节点和关系都带 projection revision；ACL 可按资源投影到节点，遍历时对每个 path node 复用 `RagPermissionFilterBuilder.build_neo4j_predicate`。这些 ACL 属性是 Resource 权威数据的派生快照，只能做 traversal prefilter，不能作为最终授权依据。

如果后续压测证明单一 relationship type 的索引/遍历性能不足，再基于真实 query profile 拆分结构边和语义边；MVP 不先做这种优化。

### 6.3 派生投影版本与提交点

chat-service 不能建立 Resource/Document 权威 manifest。为解决自身 Mongo、Qdrant、ES、Neo4j 部分写入，只新增内部 `RagProjectionCheckpoint`；名称和字段必须明确它只是可重建索引的消费进度与可见性指针：

```python
class RagProjectionCheckpoint:
    resource_id: str
    source_document_version: str
    content_hash: str
    applied_projection_revision: str | None
    staged_projection_revision: str | None
    status: ProjectionStatus  # staging / applied / failed
    updated_at: datetime
```

它不创建 Resource、不改变 document version、不决定业务资源是否 active，也不向外提供建立、删除或回滚接口。`source_document_version` 完全来自 Kafka 消息；Resource 存在性与 ACL 完全来自 resource-service。

处理一条 `DocumentReadyMessage` 的顺序：

```text
1. 读取 resourceId/version/content，计算 content_hash
2. 从 Resource 权威数据确认资源仍存在，并生成仅供索引前置过滤的 ACL projection
3. 按 (resource_id, version, content_hash) 做幂等判断，生成 staged projection_revision
4. 对 Kafka content 做 Markdown chunking；不调用 document_parse，不读取原文件
5. 从本次 chunker 已产生的 blocks/locators 构建 deterministic topology
6. 写 Mongo chunks/source refs（staged revision）
7. 写 Qdrant 和 ES（staged revision）
8. Neo4j 按 staged revision upsert nodes/edges，禁止 clean_db
9. 校验各派生后端数量、SourceRef 和 edge endpoint 完整性
10. 原子更新 checkpoint.applied_projection_revision
11. 旧派生 revision 在 grace period 后由内部维护任务清理
```

所有在线查询只读取 checkpoint 标记为 applied 的 projection revision。这个过滤只保证 chat-service 自身跨存储一致性，不声明哪个业务文档版本权威；若 Kafka 的版本顺序或回放语义需要改变，必须先修改上游事件契约，不能由 chat-service 猜测。

### 6.4 Kafka 与 Resource 权威源衔接

现有跨服务契约及职责：

| 契约 | Producer/权威方 | chat-service 行为 |
|---|---|---|
| `DocumentReadyMessage(resourceId, version, content)` | document-service | 消费正文并重建自己的 chunks/vector/graph 投影 |
| `AclRecalculateMessage(resourceId, triggerSource)` | resource-service | 从 Resource 权威数据重新生成 ACL prefilter projection，并同步 ES/Qdrant/Neo4j |
| `ResourceDeletedMessage(typedResourceIds)` | resource-service，物理销毁后发布 | 只清理消息中相关 resource IDs 的本地派生投影 |
| `checkResPermission` | resource-service 基于主档实时计算 | locate/expand 返回前校验 `VIEW`，作为最终授权结论 |

`DocumentReadyMessage` 重复消费按 `(resource_id, version, content_hash)` 幂等：同三元组 no-op；相同 source version 但 content hash 不同则建立新的派生 revision，只有全部后端写完才切换 applied checkpoint。任何失败都不提交新 applied revision，并让 Kafka 按现有重试语义处理。

新页标记适配发生在 `application/utils/chunkers/markdown/parser.py` 和 `locator.py`：parser 识别 start/end 类型，locator 只以正确配对的范围生成 page span。`TopologyBuilder` 直接消费同一次 chunking 的 `TextBlock/ChunkLocator` 结果，不新增第二套 Markdown parser，也不接入 `application/utils/document_parse`。

原 RAG 配置已为 chat-service 使用独立 consumer group：`wisepen-chat-rag-document-ready-group` 和 `wisepen-chat-rag-acl-recalc-group`。实施时应保持独立 group，不能复用 Java resource-service 的 `wisepen-document-ready-group`，否则不同服务会竞争消费同一事件。

若 Phase 2 需要 citation、footnote、hyperlink target 或 continuation 等当前 `content` 没有的结构，前置依赖是由 document-service owner 扩展 `DocumentReadyMessage`（或新增版本化事件）并由 producer 明确填充。chat-service 只能消费已发布契约，不能从源文件自行补抽取。

ACL projection 的定位必须写进接口注释和测试：它是召回性能缓存，不是权限源。原 `MongoRagAclProjectionRepository.load_resource_projection` 可以只读 Resource 主档以重建投影；最终返回仍调用 Java 实时鉴权。不要新增 ACL 管理接口，也不要把 RAG 投影写回 `wisepen_resource_items`。

### 6.5 生命周期清理和回滚

- chat-service 不提供 Resource/Document 创建、软删、硬删、版本切换或恢复接口；
- resource-service 软删除后 Resource 主档查询/实时鉴权会立即失败，locate/expand 必须因此隐藏仍残留在派生索引中的内容；
- 只有收到 `ResourceDeletedMessage` 后，consumer 才按 `typedResourceIds` 清理本地 Mongo/Qdrant/ES/Neo4j 派生投影；这不是删除业务 Resource；
- 新 source version 到达后，chat-service 只切换自己的 applied projection，并按内部保留策略清理旧派生 generation；
- 业务版本回滚必须由上游重新发布权威内容事件或执行约定的 backfill，chat-service 无权自行把某个旧 document version 宣布为当前版本；
- 工具回滚通过 feature flag 切回旧公开工具；图 schema 回滚只处理 chat-service 派生标签；
- 状态回滚可清理独立 Redis namespace，不影响 Resource 数据和 ToolContentStore；
- 任何内部清理都必须限定 resource/projection key，禁止全库 `DETACH DELETE` 或 `clean_db`。

## 7. 在线执行方案

### 7.1 locate pipeline

```text
1. Tool schema + KnowledgeNavigateActionCheck 校验 locate 参数
2. 从可信上下文读取 user/session/principal/scope fingerprint
3. 保留 trim 后 original query，创建内部 request；不做替换式 rewrite
4. 并行召回：
   a. ES exact phrase / alias / heading signal（有明确关键词时）
   b. Qdrant dense(original query)
   c. Qdrant sparse(original query)
   d. Neo4j exact canonical alias（低成本、受 ACL 过滤）
5. RagCandidateRetriever 保留每通道 rank/score，调用现有 RankingPipeline 融合
6. NodeResolver 将 chunk hit 映射到同来源的 Concept/Section/Table/Figure/Citation；没有可靠提升时返回 Chunk
7. 按 node_id 去重；同名不同来源节点不合并
8. FrontierRanker 结合 retrieval ranks、node type prior、source authority 和多样性选 max_results
9. 按 resource_id 去重并调用 ResourceClient.check_res_permission；丢弃未实时允许 VIEW 的节点
10. 对通过权威鉴权的节点 hydrate SourceRef，并计算仅包含可返回邻接的 available relation counts
11. 对 primary parent source 在 ToolContentStore 物化一次，生成 cnt_*；失败则不返回悬空 ref
12. 创建 Redis state，记录 root query、visited、paths、Kafka source versions、projection revisions 和 budgets
13. 先按结构化 item budget 裁剪，再返回完整 JSON
```

NodeResolver 的默认优先级不是绝对类型排序，而是“最小且可继续导航的语义单位”：

- hit 精确落在显式定义/受控 Concept span：返回 Concept；
- hit 落在 Table/Figure/Citation 独立 block：返回对应节点；
- hit 的 section 语义完整且不过宽：返回 Section；
- 否则返回 Chunk。

Document 节点只在 query 明确命中文档标题/元数据或作为初始结构上下文时返回，避免所有结果都坍缩到文档根。Claim 不在 MVP resolver 输出集合中。

### 7.2 expand pipeline

```text
1. 校验 expand 参数
2. Redis load state，恒定时间比较 user/session/scope/principal binding
3. 校验 graph schema/data revision 和已涉及 Kafka source versions/projection revisions；不一致即 invalidate
4. 确认 node_ids 都是该 state 已返回过的节点，拒绝任意图 ID 探测
5. 规范化 relation filter、direction、max_depth(1..2)
6. Neo4j bounded traversal：
   - 起点、每个中间节点和终点都带派生 ACL + applied projection predicate
   - 按 relation type 和 direction 过滤
   - 限制每个 seed 的内部 candidate cap 和总 path cap
   - 该层只做候选 prefilter，不把派生 ACL 当最终授权
7. 排除 visited target；保留连接新 target 所需的已访问中间节点
8. 对 focus 及每条候选路径涉及的唯一 resource_id 调用 ResourceClient.check_res_permission
   - focus Resource 失去 VIEW：state_invalidated
   - 候选路径任一 Resource 无 VIEW：整条路径丢弃，不暴露节点或计数
9. hydrate edge evidence refs 和 node source refs；无可读来源的 Tier 2 edge 丢弃
10. FrontierRanker 使用 root query + optional local focus + path features 排序
11. 可选调用现有 reranker 重排候选 preview；reranker 失败时保留确定性排序，不重复遍历
12. 选择 max_results，物化本次新增 source content refs
13. CAS 更新 state revision、focus、visited、paths、materialized refs 和 budgets
14. CAS 冲突时重新加载一次并重算 visited 差集；仍冲突则返回可重试错误，不循环
15. 返回增量 nodes/edges/paths 和权威鉴权后的 frontier count
```

expand 的局部 query 只参与本轮排序并写入 `current_focus`，不能改变 `root_query`，也不能触发一套与当前 node 无关的全库 locate。Agent 想重新选入口时应重新调用 `action=locate` 创建新 state。

### 7.3 frontier ranking

MVP 不训练模型，使用 feature rank + Weighted RRF/线性 prior：

```text
positive:
  root_query_rank
  local_focus_rank（有 focus 才启用）
  edge_confidence_rank
  relation_type_prior_rank
  path_coherence_rank
  source_authority_rank
  novelty_rank

penalty:
  visited/redundancy
  depth
  hub degree bucket
```

实现要点：

- root query 始终是主要信号，local focus 权重不能完全压过它；
- relation prior 按任务价值给定义、引用、父子结构高于宽泛 mentions；
- path coherence 奖励同一条可解释路径连续展开，而不是跨 hub 跳转；
- novelty 基于 node ID/source overlap，避免重复返回同段不同包装；
- hub penalty 使用 relation-aware degree bucket，不直接惩罚 Document/Section 的合法结构度数；
- tie-break 固定为 `(fused_score desc, path depth asc, node_id asc)`，保证测试可重复；
- feature values/ranks 写 debug trace/metrics，不进入默认模型返回。

若现有 `RankingPipeline` 只能消费 rank signal，则增加 `knowledge.navigation.frontier` preset，将每个 feature 排名转换为 `ScoreSignal`。不在 `FrontierRanker` 内再实现第二套 RRF。

### 7.4 content_ref materialization

以 parent chunk 作为 ToolContentStore 的缓存单位，原因是：

- child chunk 太碎，Agent 打开后容易缺上下文；
- 整个 Document/Section 可能过大；
- parent chunk 已是现有 RAG 的可引用原文单位，且通常小于约 6K 字符。

同一 state/source parent 只物化一次。SourceRef 同时记录 parent `content_ref` 和 node 在该 ref 内的 `content_start/end`；Section 跨多个 parent 时返回多个 SourceRef，而不是拼接一个新的超大窗口。ToolContentStore 不可用时返回 retryable error，不能先返回随后读不到的 `cnt_*`。

返回预算顺序：

1. 必要顶层字段和 state ID；
2. selected nodes 的 primary source + preview；
3. 连接 selected nodes 的 edges/paths；
4. additional source refs；
5. relation counts。

预算不足时从低优先级尾部完整删除 item，并设置 `truncated=true`；禁止按字符截断序列化后的 JSON。

### 7.5 无结果和降级

- ES 无严格命中：只要调用方没有声明必须词面过滤，继续 dense+sparse；
- graph alias 无命中：不影响普通 locate；
- locate 没有 canonical semantic node：返回 ranked Chunk，而不是空结果；
- expand 没有邻接：返回 `exhausted=true`，不自动发起新的全库检索；
- reranker 不可用：用确定性 feature rank；
- Neo4j 不可用：locate 仍可返回结构节点取决于结构投影是否有可用 repository，但不能伪造 expand 成功；明确返回 backend unavailable；
- Redis 不可用：不能创建/更新可靠 state，整个调用失败，不退化为无状态工具；
- ToolContentStore 不可用：不返回无效 content ref。

## 8. 分阶段实施

### 8.1 Phase 0：前置一致性修复

目标：在不暴露新工具前，对齐真实 Kafka/Resource 契约，并建立稳定的派生索引身份与可见性。

修改/新增：

- `domain/entities/rag_corpus.py`：复合 projection key 和 `RagProjectionCheckpoint`；
- `application/rag/ingestion/models.py`、`chunking.py`、`ingester.py`：只消费 Kafka content，传播 source version 和 projection revision；
- `application/utils/chunkers/markdown/parser.py`、`locator.py`：对齐 Java `page:start/page:end` 正文标记并生成成对 page spans；
- `core/persistence/mongo/rag_corpus_repository.py`：按 resource/version/chunk 读取，禁止裸 chunk ID；
- `core/persistence/qdrant/rag_repository.py`、`core/persistence/elasticsearch/rag_repository.py`：复合 ID 和 applied projection revision；
- `application/rag/graph/graphrag_builder.py`、`core/persistence/neo4j/rag_repository.py`：去除 `clean_db`，按 revision upsert/delete；
- `application/rag/retrieval/models.py` 及各 filter：支持 applied revision，修复完整 group role scope；
- `application/rag/acl/**`：明确本地 ACL 是 derived prefilter，禁止写 Resource 权威集合；
- `application/rag/kafka_consumers/resource_deleted_consumer.py`：消费既有物理销毁事件，只清理 chat-service 派生投影；
- `service_client/resource_service_client.py`：复用现有实时 `VIEW` 校验，不新增 Resource mutation API。

测试：新 page marker 契约、跨资源相同 chunk、同消息重试、staged 写入部分失败、applied checkpoint 切换、ACL projection 重建、Resource 软删实时拒绝、物理销毁事件清理派生数据。

验收：RAG 不调用 document_parse；新 page locator 正确；任一后端失败不会让 staged revision 可见；四个后端使用同一复合身份；现有 RAG 只读 applied revision；所有返回结果通过 Resource 实时 `VIEW` 校验。

主要风险：存量索引迁移。采用双写/影子校验，旧 collection/index 保留到新读路径验证通过。

### 8.2 Phase 1：可靠文档拓扑 + 基础导航

新增：

- `application/knowledge_navigation/models.py`
- `application/knowledge_navigation/repository_protocols.py`
- `application/knowledge_navigation/navigator.py`
- `application/knowledge_navigation/locator.py`
- `application/knowledge_navigation/traverser.py`
- `application/knowledge_navigation/node_resolver.py`
- `application/knowledge_navigation/frontier_ranker.py`
- `application/knowledge_navigation/result_builder.py`
- `application/knowledge_navigation/indexing/topology_builder.py`
- `application/knowledge_navigation/indexing/projection_models.py`
- `application/tools/knowledge_navigation/knowledge_navigate_tool.py`
- `application/tools/knowledge_navigation/action_check.py`
- `core/persistence/neo4j/knowledge_navigation_repository.py`
- `core/persistence/redis/knowledge_navigation_state_repository.py`

修改：

- `application/rag/retrieval/retrieval_pipeline.py`：抽取共享 candidate retrieval；
- 新增或从 pipeline 提取 `application/rag/retrieval/candidate_retriever.py`；
- `application/utils/ranking/presets.py`：注册 navigation locate/frontier preset；
- Markdown chunker 边界：TopologyBuilder 直接复用 Kafka content 本次 chunking 产生的 `TextBlock/ChunkLocator`，不新增 parser；
- `application/rag/ingestion/ingester.py`：调用 topology builder 并写 Neo4j projection；
- locator/traverser/result builder：返回前通过 `ResourceClient.check_res_permission` 做唯一权威 `VIEW` 校验；
- `container.py`、`main.py`、`core/config/app_settings.py`：装配、TTL/limits 和单工具 feature flag；
- tool providers/registry：只暴露 `knowledge_navigate`。

范围：Document、Section、Chunk、可靠 Table/Figure/Citation、受控 Concept；确定性结构边；locate/expand；Redis state；SourceRef/content_ref；全链 ACL。

测试：contract、locator、resolver、topology、direction/relation traversal、每跳 ACL、state binding/CAS/TTL、content ref、budget、版本失效和集成 happy path。

验收：

- Agent 能从一次 locate 沿 1～2 跳结构边连续阅读；
- 所有返回节点能用 `cnt_*` 回读原文；
- 无跨权限/旧版本泄露；
- 同输入、同索引 revision 的结果顺序稳定；
- 模型侧只看到一个知识库工具。

明确不实现：PPR、Community、完整 Claim、全量关系抽取、自由 Cypher、自动多轮 traversal、三跳以上、Global Search、在线训练 selector。

### 8.3 Phase 2：Concept / Claim / 来源链

新增/扩展：

- `application/knowledge_navigation/indexing/semantic_relation_builder.py`；
- `Concept` authority/alias 模型；
- `Claim`、`ClaimRole`、qualifier 和 multi-source SourceRef；
- 离线 extraction audit dataset 和 extractor version registry；
- Neo4j relation/claim repository 查询；
- 若确有来源链需求，由 document-service owner 评估扩展 `DocumentReadyMessage` 或新增版本化 Kafka 事件，显式提供 citation、footnote、hyperlink target 和 continuation 信号；chat-service 只增加对应 consumer DTO 和投影逻辑。

只提升满足下列任一条件的 relation 为 Claim：有条件/时间/适用范围、作者立场、多来源支持/冲突、需要独立打开、或 subject/object 二元边不足以表达。简单且明确的 `DEFINED_IN` 等可以继续使用 evidence-backed edge。

测试：同名概念消歧、多来源 Claim、qualifier 保真、低置信候选不入图、extractor version 重建、citation/source chain、冲突来源并列展示。

验收：Tier 2 edge grounding rate 达到门槛；任一语义边都能回到证据 span；Claim 投影和打开后的限定条件一致。

主要风险：LLM 抽取幻觉和概念 hub。采用白名单 relation、显式 evidence span 校验、文档内默认 concept scope、度数上限和离线抽样审计；不合格 extractor 可按 version 整批禁用/重建。

### 8.4 Phase 3：依赖、冲突和动态桥接

候选能力：

- `DEPENDS_ON/REQUIRES/IMPLEMENTS/DERIVED_FROM/CONTRASTS_WITH/SUPERSEDES/CONTRADICTS` 的领域模板；
- 查询时 `POSSIBLE_BRIDGE` 候选，不写正式事实；
- PPR/seed propagation 作为 frontier rank 的可切换实验；
- 仅对明确编号步骤、程序或 workflow 构建 Process-like node；
- 评测证明需要后再增加同一工具内的 `path` action。

测试/评测采用离线 replay 和 shadow traffic，与 Phase 1 bounded BFS baseline 对照。PPR 必须单独报告 hub exposure、source grounding 和路径可解释性；不能只看最终召回率。

验收：相对 baseline 显著减少无效 expand/调用轮数，且不降低 ACL 正确率、source grounding rate 或 p95 latency 门槛。未达到即保留实验 flag，不进入默认路径。

## 9. 测试与评测

### 9.1 自动化测试矩阵

| 场景 | 核心断言 |
|---|---|
| 权限隔离 | 派生 ACL 只做 prefilter；locate/expand 返回前均通过 resource-service 实时 `VIEW`，无权节点、边、路径和 relation count 全部不返回 |
| ACL 变化 | 即使本地 projection 尚未刷新，Java 实时鉴权拒绝后也不得返回；focus 失权时旧 state `state_invalidated` |
| Resource 软删除 | 主档移出业务集合后实时鉴权立即拒绝，即使 ES/Qdrant/Neo4j 仍有残留 |
| Resource 物理销毁 | 只在收到 `ResourceDeletedMessage` 后清理本地派生数据，不调用 Resource 删除接口 |
| state 伪造 | 随机 ID、错误 user/session 返回同一 not-found 外观 |
| state 并发 | 两次 expand CAS 不丢 visited/path 更新；最多一次有限重试 |
| 状态体积 | 达到 node/edge/path budget 后可预测停止，不超 Redis size 门槛 |
| 节点去重 | 同一 canonical ID 只返回一次；同名不同来源不误合并 |
| 方向遍历 | in/out/both 在每种关系上返回预期邻接和 direction 投影 |
| 关系过滤 | 只出现允许 relation；未知 relation 在 preflight 拒绝 |
| 路径正确性 | node 数 = edge 数 + 1，深度和边方向一致，无不可读中间节点 |
| 最大深度 | 只允许 1～2；图查询内部也强制 cap，不能只靠 schema |
| 重复 expand | 已 visited target 不重复；连接新 target 的旧中间点不重复计 node |
| content_ref | 每个 ref 可由现有 reader 读取，source/content offsets 对齐原文 |
| 缓存失效 | state TTL 不长于 content TTL，不返回过期 ref |
| Kafka 内容契约 | `DocumentReadyMessage` 只映射 resourceId/version/content；RAG 不调用 document_parse |
| 页标记契约 | `page:start/page:end` 正确配对并生成 page locator；缺失/错序时 fail closed，不伪造 page span |
| 内容版本 | staged projection 不可见，checkpoint applied 后只见新 source version，旧 state 失效 |
| 摄入失败 | 任一后端失败不会应用部分 projection revision，重复事件按 resource/version/hash 幂等 |
| chunk ID 碰撞 | 跨 resource/version 相同内容和 index 仍有不同 projection/node ID |
| 高连接 hub | MENTIONS/通用实体不会挤占全部 top results，degree penalty 可观测 |
| token budget | 输出始终是完整 JSON；裁剪设置 truncated，不切半 item |
| 无结果 fallback | canonical node 不足时落到 Chunk；expand 无邻接返回 exhausted |
| 原始 query | locate 和所有 rank trace 保留原 query；local focus 不覆盖 root |
| Tier 分层 | Tier 2 无 evidence 拒绝写入；Tier 3 不出现在正式图 repository |

建议测试文件：

```text
src/chat/tests/knowledge_navigation/test_tool_contract.py
src/chat/tests/knowledge_navigation/test_locator.py
src/chat/tests/knowledge_navigation/test_node_resolver.py
src/chat/tests/knowledge_navigation/test_topology_builder.py
src/chat/tests/knowledge_navigation/test_traverser_acl.py
src/chat/tests/knowledge_navigation/test_frontier_ranker.py
src/chat/tests/knowledge_navigation/test_navigation_state.py
src/chat/tests/knowledge_navigation/test_result_builder.py
src/chat/tests/knowledge_navigation/test_projection_checkpoint.py
src/chat/tests/knowledge_navigation/test_kafka_content_contract.py
src/chat/tests/knowledge_navigation/test_resource_authorization.py
src/chat/tests/knowledge_navigation/test_navigation_integration.py
```

### 9.2 离线评测集

建立不少于三类任务，每条都标注入口节点、可接受路径和最终 source spans：

1. **定位题**：定义、章节、表格、图、引用的直接查找；
2. **多跳阅读题**：从概念到定义、前置要求、实现说明或引用来源；
3. **歧义/权限题**：同名概念、多版本、跨资源边、不可读邻居和 hub。

传统 RAG baseline 不是一次调用，而是允许 Agent 反复调用旧 RAG 到同一轮数/延迟预算；这样才能公平回答“导航是否更高效”。两组使用相同原始语料、embedding、reranker、模型、token 和 ACL。

### 9.3 指标和建议门槛

| 指标 | 定义 | Phase 1 建议门槛 |
|---|---|---:|
| 入口定位准确率@K | gold 起点或可接受 ancestor 出现在 locate Top-K | 离线基线 +5pp 或不低于现有 RAG |
| 关系路径准确率 | 返回路径的 node/edge 序列被标注接受 | >= 90%（Tier 1） |
| source grounding rate | 返回节点/语义边具有可读且支持它的 source ref | Tier 1/2 均 100% contract；抽样语义支持率另报 |
| 重复节点率 | 非必要重复 node / returned nodes | < 5% |
| 无效扩展率 | 没有推进 gold task 的 expand / all expands | 比反复 RAG 降低 >= 20% |
| 平均调用轮数 | 完成同一阅读任务的 tool calls | 比反复 RAG 降低 >= 15% |
| 平均返回 token | 每次及每完成任务的 tool output token | 不高于 baseline |
| p50/p95 latency | locate、expand 分开报告 | 由当前 RAG SLO 定绝对值；expand p95 不超过 locate p95 |
| 阅读任务成功率 | Agent 找到全部要求证据且引用正确 | 比 baseline 提升 >= 10pp |
| ACL violation | 任意不可读信息泄漏 | 0 |
| hub exposure@K | Top-K 中由单一高频 hub 引入的候选占比 | < 30% |

这些数字是上线 gate 的起始建议，首轮 benchmark 后按现有 RAG 实测 SLO 校准。不能用调低任务难度满足门槛。

### 9.4 可观测性

每次调用记录不含原文的结构化 trace：action、state revision、backend latency、candidate counts、过滤 counts、rank preset/version、truncated/exhausted、content materialization count、state bytes、graph/schema/data revision。query 及 preview 是否进入日志遵循现有敏感数据策略，默认不记录全文。

告警：Resource 实时鉴权失败/超时、派生 ACL 与实时鉴权结果分歧率、ACL event lag、applied projection mismatch、悬空 source ref、CAS 冲突率、hub concentration、Neo4j candidate cap、输出预算裁剪率和 content ref 读取失败率。

## 10. 最终实施清单

按依赖顺序执行；每项完成后再进入下一项，不并行上线相互依赖的读写协议。

| # | 任务 | 涉及文件 | 前置依赖 | 验收标准 | 主要风险 |
|---:|---|---|---|---|---|
| 1 | 合并并基线化原 RAG | `application/rag/**`、`container.py`、现有 RAG tests | 无 | formal 分支现有测试 + RAG tests 通过 | 分支差异 |
| 2 | 对齐 Kafka page marker | Markdown chunker parser/locator、consumer contract tests | 1 | 新 `page:start/page:end` 生成正确 page spans；不调用 document_parse | 历史消息兼容 |
| 3 | 固化 Resource 权威边界 | ACL projector/repository、`ResourceClient`、检索返回过滤 | 1 | 本地 ACL 只做 prefilter；最终结果全部通过 Java 实时 `VIEW` | RPC 延迟/可用性 |
| 4 | 复合 chunk/projection identity | chunking、Mongo/Qdrant/ES/Neo4j repositories | 1 | 跨资源/版本无 ID 碰撞 | 存量迁移 |
| 5 | 派生 projection checkpoint | corpus entity/repository、ingester、filters | 4 | 多库部分失败不 applied；不具备 Resource/Document mutation 能力 | filter 规模 |
| 6 | Resource 销毁事件 consumer | Kafka consumer、四个派生 repositories | 3～5 | 只按 `ResourceDeletedMessage` 清理本地投影；无 Resource 删除调用 | 软删残留窗口 |
| 7 | Neo4j 增量写入 | graph builder、RagNeo4jRepository | 4、5 | 无 `clean_db`；仅按 resource/projection key upsert/cleanup | 旧图清理 |
| 8 | 共享 candidate retrieval | retrieval pipeline、`candidate_retriever.py` | 1、3、5 | 旧 RAG 结果无回归；locator 不复制召回逻辑 | 隐式 gate 耦合 |
| 9 | Kafka 正文 topology input | chunker 输出、projection models | 2、4 | 同一次 chunking 的 block/locator 可重复投影，无第二套 parser | 上游内容信息有限 |
| 10 | deterministic topology builder | `indexing/topology_builder.py` | 7、9 | gold content 的结构节点/边 100% 可回源 | 错误结构推断 |
| 11 | navigation domain models/protocols | `models.py`、`repository_protocols.py` | 4、10 | 类型、ID、source/edge invariants 单测通过 | 模型过宽 |
| 12 | Neo4j traversal repository | `knowledge_navigation_repository.py` | 3、7、11 | direction/relation/depth/派生 ACL prefilter 测试通过 | Cypher 膨胀 |
| 13 | Redis state repository | `knowledge_navigation_state_repository.py` | 11 | binding、TTL、limits、CAS、source/projection 失效测试通过 | 并发更新 |
| 14 | locator/resolver | locator、node resolver、shared retriever | 8、10、11 | 原 query 保留；Chunk fallback；返回前实时 `VIEW` | 过度提升/RPC 放大 |
| 15 | traverser/frontier ranker | traverser、ranker、ranking presets | 12～14 | 路径全 Resource 实时鉴权、deterministic rank、no repeats | 排序偏置 |
| 16 | result builder/content refs | result builder、ToolContentStore adapter | 11、13～15 | 所有 source ref 可读且已授权；无悬空 cnt_* | TTL/缓存失败 |
| 17 | tool contract/entry | tool、action check、navigator | 13～16 | locate/expand contract 和错误语义通过 | action 歧义 |
| 18 | 装配、集成和单入口灰度 | container、main、settings、registry、benchmark | 17 | 单一工具、ACL violation=0、指标过 gate、flag 可回滚 | 生产规模 |
| 19 | 上游结构事件契约（条件任务） | Java `DocumentReadyMessage`/producer，由 document-service owner 实施 | Phase 1 证明当前 content 不足 | 新字段版本化且 producer/consumer contract tests 同步 | 跨服务协调 |
| 20 | Phase 2/3 语义与传播实验 | semantic builder、Claim、experimental ranker/path | 18；需要结构字段时依赖 19 | evidence 全可回源；相对 BFS 有收益且 ACL 不下降 | 抽取幻觉/hub |

## 附录 A：20 个开放问题的明确答案

1. **`locate` 返回 Chunk 还是 Concept/Claim 为主？** 以最小可导航 canonical node 为主：可靠 Concept、Section、Table、Figure、Citation 优先，Chunk 是不可可靠提升时的 fallback；MVP 不返回 Claim。
2. **Concept 如何生成稳定 ID？** 权威 glossary/external ID 使用 scope namespace + canonical key；非权威概念使用 resource/version + normalized label + definition span/source discriminator 的 hash，禁止只按名称生成。
3. **同名概念如何消歧？** 默认不合并；返回来源、section 和类型上下文。只有共享权威 ID 或经审计的 alias mapping 才合并。
4. **Section 和 Chunk 是否都需要成为图节点？** 需要。Section 表达层级和阅读上下文，Chunk 是检索/回源和细粒度 fallback；两者职责不同。
5. **`MENTIONS` 会不会造成超级 hub？** 会。MVP 只对受控概念/可靠实体建立，默认 expand 关系集不全局包含 MENTIONS，并使用 relation-aware degree cap/penalty。
6. **如何限制通用实体节点造成图坍缩？** 文档内默认 concept identity、权威 ID 才跨文档合并、停用词/类型白名单、最大 degree、hub penalty，并禁止仅凭字符串同名合并。
7. **expand 默认 direction？** `both`。文档边的存储方向不适合要求 Agent 预先理解；返回和排序仍保留相对方向语义。
8. **max_depth 是否限制 1～2？** 是。schema 和 repository 双重限制；三跳以上必须多次 expand，便于 ACL、预算和可解释路径控制。
9. **路径是否保存完整节点序列？** 保存完整 node IDs + edge IDs，但只保存已返回路径，且单路径深度最多 2；不复制节点 payload。
10. **state 是否保存完整 frontier？** 不保存。只保存 visited、paths、budgets 和 materialized refs，frontier 从调用方指定的 node IDs 重算。
11. **state_id 绑定哪些权限和版本信息？** user、session、opaque scope fingerprint、principal fingerprint、已涉及 Kafka source versions、chat projection revisions、graph schema/data revision；Resource 权限不依赖本地 revision，而是在每次 expand 实时重验。
12. **图更新后旧 state 怎么处理？** revision 不一致即 `state_invalidated` 并要求重新 locate；不尝试把旧 node/path 静默迁移到新图。
13. **content_ref 指向 Chunk、Span 还是窗口？** 指向 ToolContentStore 中物化的 parent chunk；SourceRef 另带 node span 在该 content 内的 offsets。Section 跨 parent 时有多个 refs。
14. **一个 Claim 对应多个 source span 怎么表达？** Claim 拥有有序/去重的多个 SourceRef，可在 qualifiers 中区分 support/oppose/context 角色；Agent 投影的 edge 引用对应 ref IDs。
15. **如何区分三层关系？** `origin=deterministic|explicit_text|query_candidate`；Tier 3 不写正式图，只有当次 candidate 身份，不能作为事实边返回。
16. **边是否需要 origin/confidence/extractor_version？** 需要。Tier 1 confidence 固定 1.0、extractor 可为空/结构 builder version；Tier 2 三者和 evidence refs 均必需。
17. **HippoRAG PPR 是否适合 MVP？** 不适合。先用有界 BFS + deterministic rerank；PPR 只作为 Phase 3 与 BFS 对照的可关闭实验。
18. **Process-like structure 如何用于通用文档？** 仅对原文明示的编号程序、工作流、方法步骤离线构建；普通列表、相似段落或会话阅读路径不写 Process node。
19. **哪些关系预计算，哪些查询时发现？** 结构关系、显式引用、审计后的 Tier 2 预计算；语义相似、shared entity bridge、possible dependency 只在查询时作为候选。
20. **如何证明比反复传统 RAG 高效？** 在相同语料、ACL、模型、token/延迟预算下做 Agent task A/B；比较成功率、平均调用轮数、无效扩展率、总返回 token、p95 latency 和 source grounding，而不是只比较单次 recall。

## 附录 B：关键风险与停止条件

| 风险 | 预防/检测 | 回滚或停止条件 |
|---|---|---|
| ACL 泄漏 | 派生 prefilter + Resource 实时 `VIEW`、路径全 Resource 校验、负向权限测试 | 任一泄漏立即关闭新工具 |
| 派生 ACL 陈旧 | 监控 projection 与实时鉴权分歧；实时结果始终优先 | Resource RPC 不可用时 fail closed，不降级到本地 ACL |
| ID/版本混读 | 复合 ID、applied projection checkpoint、integration tests | 切回旧读路径，保留 staged 索引排查 |
| 多库部分成功 | staged revision + applied checkpoint | 不切 applied；Kafka 重试或清理 staged |
| Kafka marker 漂移 | Java/Python contract fixture 覆盖 `page:start/page:end` | page locator 测试失败即阻断摄入发布 |
| 结构关系误判 | 只消费 Kafka content 明示结构，不能推断 continuation/citation | 禁用相应 builder version 并重建派生图 |
| 越权修改 Resource | chat-service 无 Resource mutation client/API；只读/鉴权调用审计 | 出现 create/update/delete Resource 调用即阻断发布 |
| 语义抽取幻觉 | evidence span mandatory、白名单、离线审计 | grounding 低于 gate 不进入 Phase 2 默认路径 |
| Hub 垄断 | degree metrics、relation prior/cap、gold hub tasks | Top-K hub exposure 超门槛则关闭该 relation |
| state/content 失配 | state TTL <= content TTL、写 ref 失败则整体失败 | 清理 navigation namespace，重新 locate |
| token 膨胀 | item budget、增量返回、完整 JSON | 裁剪率持续过高则下调 max_results/关系计数 |
| latency 回归 | backend 分段 metrics、candidate/path caps | 超 SLO 切回旧工具并做索引/query tuning |
| 双入口行为漂移 | registry 互斥 feature flag、scope snapshot test | 发现同时暴露立即阻断发布 |

Phase 1 发布的硬性停止条件是：ACL violation 非零、最终返回绕过 Resource 实时鉴权、RAG 调用 document_parse、chat-service 出现 Resource mutation 接口、存在不可回读 content ref、非 applied projection 可见、输出 JSON 被截断、或 tool registry 同时暴露两个重叠入口。任何一项出现都不能以“后续优化”名义灰度上线。
