# P5 第二片：Neo4j 多来源拓扑投影与 repair 装配

记录日期：2026-10-12。实现前 HEAD 为 bd06df2d，上一片抽取/本体已提交；本片未提交，两个既有子模块状态保留。实际入口为 storage/graph.py、ingestion/indexers/graph.py、backends/neo4j/，以及扩展后的 scripts/repair_graph.py。

## 来源与接口

GraphNode/GraphEdge/Ontology 保持 dataclass。GraphSourceProjection 是按 Chunk 分组的实际写入输入，只有 chunk_id、普通 metadata、nodes、edges；不是另一套事实模型或 Mongo 集合。Indexer 填入 resource_id/content_revision，缺失图列表或结构/来源损坏在任何写入前报错，本体非法单个事实逐条丢弃，同来源重复 ID 保留首次合法事实。

Writer.replace_sources 只替换明确的来源，空成功移除旧贡献；delete_sources 必须非空条件。Reader 按 ID 返回来源事实，支持方向/谓词/逻辑关系数量上限的一跳，默认 limit=100，不截断选中关系的匹配来源。ID 批量读取按请求 ID 和 Chunk ID 排序，一跳按关系 ID 和 Chunk ID 排序。没有 Receipt、发布状态或图检索模式。

Neo4j 实例绑定 driver/database/graph_name，默认 neo4j/rag_v4，来源写入批大小 64。图元由 graph_name 专属实体、关系、来源标签及唯一约束/资源 revision 索引隔离；关系图元通过 FROM/TO 指向端点，保留有向谓词身份。共享图元不保存合并描述，各来源关联用 JSON 保存对应 GraphNode/GraphEdge 事实。嵌套关系 metadata 不强制字符串化，不可 JSON 序列化在写前报错。

MetadataFilters 支持现有 EQ/IN/范围、AND/OR/NOT，键和值参数化。来源属性加后端 meta_ 前缀，只有平面 key；缺失属性不匹配，NOT 做布尔取反。Storage 不解释字段业务、不判 ACL 或选择 active。

## 事务与 repair

每批显式 Neo4j 事务：移除本批旧来源 → 写入非空来源和事实 → 清理本次触碰的无来源关系/实体 → 等待提交。每个写结果消费完成，节点/关系写入行数需匹配，避免 MATCH 缺端点静默丢写。ensure_schema 显式执行，CRUD 不初始化，不自动重试。

写入阶段异常/取消回滚当前批，之前提交批次保留。提交确认网络异常可能服务端已经提交，不能断言旧贡献一定保留；异常传播且不报告成功，重跑按来源幂等替换恢复。不增加全局锁、租约、第二发布指针或跨库事务，同资源由调用方串行。

repair 默认事实模式保持；--with-graph 才创建 driver 和初始化 schema。Neo4j URI/username/password/database/graph_name 使用 CLI 或 RAG_GRAPH_REPAIR_NEO4J_*，启用时连接前校验。顺序为 applied 身份读取 → 抽取/缓存复用 → 写前状态复核 → ChunkFactsIndexer → GraphIndexer。缓存全复用仍重写全部来源；事实成功、图失败保留事实，计资源错误并继续，重跑不需要重复调用模型。所有客户端在正常/异常/取消路径关闭。

## Reference 与边界调整

参考 LightRAG commit 453dce83d6d0354a06e46c8d4029a0895c4e054b：operate.py 的来源跟踪与合并、lightrag.py 的贡献删除、neo4j_impl.py 的批量 UNWIND/结果消费。保留完整来源与删除后维护共享图元的能力；不引入其 KV tracking 集合、自动重试、来源截断或跨来源模型摘要。其 adapter 使用无向 MERGE，本片按已冻结 GraphEdge 身份保留方向/谓词。旧 WisePen reified relation/source 结构只作适配参考，不迁入 ACL 投影、旧 Repository 或隐式 schema 初始化。

用户追加确认：VectorRecord/VectorQuery/VectorMatch 与 Reader/Writer 合并到 storage/vector.py，原 core/schema/vector.py 和 core.schema 导出移除，无兼容别名。Indexer、Qdrant、Retriever、application 和测试同步更新；类型字段和召回/发布行为不变。泛用 filters 继续属于 core.schema。

## 验证与未验证项

- RAG 全服务：664 passed / 21 skipped；Common Markdown/AI clients/retrieval：125 passed。
- 向量迁移定向：156 passed / 3 skipped。Neo4j/Indexer/repair 定向覆盖 来源/方向/过滤、幂等/空成功/孤儿、结果与 commit 确认、前批保留/当前批失败、Mongo 事实到拓扑、repair 缓存重跑和关闭。
- Neo4j 驱动替身验证事务与参数合同，不等于真实 Cypher 语义验证。3 个 Neo4j 集成测试需 RAG_TEST_NEO4J_URI、可选 username/password/database，随机 graph_name 隔离，仅清理自己的标签和约束。未配置，实际跳过；21 项合计 12 Mongo、3 Kafka、3 Qdrant、3 Neo4j。
- 修改的 24 个 Python 文件 Ruff check/format check 通过；compileall、git diff --check、uv lock --check --offline、依赖边界和 repair CLI help 检查通过。已有 contextualizer.py 的 I001 导入顺序问题保持，不扩大修改。

没有真实后端/模型联调或业务 repair。图向量、GraphRetriever、受授权的 local/global/hybrid、生产生命周期未实现。下一片从实体/关系向量与 application 授权查询继续；读取时事实正文/描述仍须由授权 Mongo 来源回查，不把拓扑 JSON 升为权威。历史 revision、重分块旧 Chunk、资源全面清理仍留后。装配和 CLI 见 [demo/graph.md](../demo/graph.md)。
