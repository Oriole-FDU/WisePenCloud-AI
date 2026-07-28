# RAG 实现 TODO

## 当前原则

- Java Kafka 事件决定何时刷新正文与 ACL；Resource Mongo 是资源和预计算 ACL 的权威数据源。
- 查询热路径只使用本地 ACL 投影，不调用 `checkPermission`。
- Qdrant 负责 dense/BM25 native RRF 主召回和权限预过滤。
- Mongo 保存可回读正文、结构投影和 applied revision；Neo4j 只保存跨文档实体关系。
- Mongo ACL 投影是查询返回前的最终授权门；Qdrant/Neo4j 的 ACL 用于缩小候选集。
- 新实现直接使用当前 chunker、ranking 和 tool content API，不保留旧 RAG 兼容层。

## TODO

- [x] 对照 `wisepen-old`、当前 chat service、Java Kafka DTO 和 Resource ACL 数据结构。
- [x] 完成 ACL 纵向链路。
  - [x] 定义 `VIEW` 权限投影和请求权限范围。
  - [x] 从 `wisepen_resource_items` 投影 owner、指定用户和 computed group ACL。
  - [x] 为 Qdrant、Neo4j 生成等价预过滤条件。
  - [x] 持久化 ACL 投影并接入 `AclRecalculateMessage` consumer。
  - [x] 将 ACL 更新同步到已存在的检索后端记录。
  - [x] 候选和图路径返回前批量执行 Mongo ACL 二次授权。
- [x] 完成 Kafka 正文摄入与结构投影。
  - [x] 严格解析 `DocumentReadyMessage(resourceId, version, content)`。
  - [x] 以 SectionNode 作为文档内语义节点，构建 `SectionNode -> ReadingBlock -> RetrievalChunk` 投影。
  - [x] ReadingBlock 严格属于单个 Section；短 Section 一个块，长 Section 多个块。
  - [x] RetrievalChunk 严格属于单个 ReadingBlock 和单个 Section，仅用于 dense/BM25/reranker。
  - [x] SourceRef 保存 RetrievalChunk 到 Kafka 正文 span 和 Section 的精确证据映射。
  - [x] 实现 staged/applied content revision。
- [x] 完成内容索引。
  - [x] Mongo 保存权威正文引用和结构投影。
  - [x] Qdrant 写入 dense/sparse 检索点与 ACL payload。
  - [x] 更新只替换当前 Resource revision 的派生数据。
  - [x] 按 embedding model 与 contextual index text 复用未变化 leaf 的向量。
- [x] 完成 RAG 查询链路。
  - [x] 从可信请求上下文构建 `RagPermissionScope`。
  - [x] Qdrant `Prefetch(dense, BM25) + FusionQuery(RRF)` native hybrid retrieval。
  - [x] 复用当前 `RankingPipeline` 排序和去重。
  - [x] 将 RetrievalChunk 命中归并到 Section，并物化命中 ReadingBlock 与 SourceRef。
  - [x] 返回可继续导航的 SectionView，而不是静态拼接 ancestor/previous sibling 正文。
- [x] 完成 Agent 工具。
  - [x] 实现 `knowledge_navigate.locate`。
  - [x] 实现 state 约束的 `knowledge_navigate.expand`。
  - [x] 将返回正文写入 `ToolContentStore`。
  - [x] 实现最小 Redis navigation state。
  - [x] locate 返回命中 Section、ReadingBlock 和标题树 frontier。
  - [x] `knowledge_navigate_sections` 按 Section ID 读取正文及展开 parent/previous/next/children。
- [x] 完成跨文档图导航。
  - [x] 接入 `neo4j-graphrag` 实体关系抽取。
  - [x] 完成 evidence、EntityResolver 和 revision 写入。
  - [x] 实现 ACL 约束的一至两跳 `expand`。
  - [x] 按抽取窗口内容缓存 LLM 结果，并在命中后重新定位 evidence。
- [x] 优化结构化召回。
  - [x] SectionNode 成为文档内检索、阅读和多跳的统一语义节点。
  - [x] 使用 SectionNode、ReadingBlock、SourceRef 建立结构节点与 retrieval leaf 映射。
  - [x] contextual index text 加入稳定的 section path 和 section opening。
  - [x] 更新时只重新调用变化 leaf 的 embedding 和变化窗口的 LLM。

## 标题树主索引重构

- [x] 删除独立于 Section 边界生成的 `RagParentChunk` 及其持久化、物化和工具输出。
- [x] 增加 `RagSectionReadingBlock`；块 ID、顺序、原文范围和 Section 归属均可稳定回读。
- [x] 改写 RAG projector：先构建 SectionTree，再只在每个 Section 的 `own` 范围内生成 ReadingBlock 和 RetrievalChunk。
- [x] 长 Section 的多个 ReadingBlock 保持顺序；页码只影响块边界和 locator，不改变 Section 身份。
- [x] Qdrant payload 保存 `section_id` 和 `reading_block_id`，检索结果按 Section 去重/归并。
- [x] Mongo 保存 Section、ReadingBlock、RetrievalChunk 和 SourceRef 的同 revision 投影。
- [x] 删除静态 section context 拼装，改为查询时生成 `RagSectionView`：
  - [x] 当前 Section 元数据与摘要。
  - [x] 命中 ReadingBlock 和精确 evidence。
  - [x] parent、previous、next、children 的轻量标题树 frontier。
  - [x] frontier 只返回 ID、标题、路径、摘要和是否可继续展开，不预加载邻接正文。
- [x] 增加 Section 树导航服务：按 applied revision 批量加载 SectionView、展开 frontier、读取指定 ReadingBlock。
- [x] 文档内 Section 多跳与 Neo4j 跨文档实体多跳保持独立边界，在 knowledge navigation service 汇合。
- [x] 删除旧 Section context、ParentChunk 命名和无消费者代码，不保留兼容导出。
- [x] 更新 README 和标题树方案，使 PageIndex 的“树选择节点，再按需读正文”成为唯一口径。
- [x] 测试覆盖：
  - [x] 短 Section 一个 ReadingBlock，长 Section 多块且均不跨 Section。
  - [x] RetrievalChunk 只属于一个 ReadingBlock/Section，source spans 精确回读 Kafka Markdown。
  - [x] 多个 chunk 命中同一 Section 时只返回一个 SectionView，并保留最优 evidence。
  - [x] Section frontier 的 parent/previous/next/children 顺序稳定且不携带邻接正文。
  - [x] Agent 可从 locate 命中的 Section 连续展开两跳并按需读取正文。
- [x] 消费 `wisepen-resource-physical-destroy-topic`，先关闭 Mongo ACL gate，再幂等清理其余派生数据。
- [x] 完成运行时与验收。
  - [x] 容器、配置、Kafka lifecycle 和数据库初始化。
  - [x] 单元测试、repository contract test 和最小集成 fixture。
  - [x] 使用 Nacos 实际配置连通 Mongo、Redis、Qdrant 和本地 Neo4j。
  - [x] 在 Neo4j 5 上验证 projection 幂等写入、mention 解析和六种有界遍历。
  - [x] 验证真实 Mongo/Neo4j ACL、物理删除和 Redis extraction cache。
  - [x] 100 Resource 的 Mongo ACL 批量授权约 20 ms（本地实测）。
  - [x] Nacos 配置下完成真实 embedding、服务端 `qdrant/bm25` 与 native RRF 端到端验证。

## 本轮验收

- `uv run pytest src/chat/tests -q --basetemp <repo-local-dir>`：237 passed。
- Nacos 实际配置下完成 `chat.main` 导入和 RAG 容器依赖解析。
- 真实 Mongo + 本地 Neo4j 完成 owner/denied ACL、关系遍历和物理删除闭环。
- 真实 Redis 完成 extraction cache 读写；测试键和临时 ACL 文档均已清理。
- Qdrant repository contract 测试覆盖 ACL payload、revision point ID、向量复用和物理删除；真实 Qdrant 完成 dense + 服务端 BM25 + native RRF 验证，临时 collection 已删除。
