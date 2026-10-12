# 通用平台装配与服务生命周期

默认入口为 `rag.main:app`，配置沿用 `app_settings.settings` 从 Nacos 加载、`container.py` 声明 provider/默认实例、`main.lifespan` 启停的项目习惯。运行配置样例见 [platform-settings.yaml](platform-settings.yaml)，引导环境沿用 bootstrap_settings 中的 Nacos 地址、服务身份等字段。新运行配置要求 Mongo、Qdrant、embedding 模型/维度和 Qwen rerank 完整，旧的仅 ACL 配置不再足够。

默认链路 `MarkdownChunker → [Contextualizer] → ChunkFactsIndexer → VectorIndexer`。`ENABLE_CONTEXTUALIZE=false` 默认不增强；事实保存和普通向量写入始终执行。默认不抽取图事实，扩展包显式给工厂传 Ontology。垂类通过 `platform.pipeline_routes()["history"] = history_pipeline` 注册摄入链，平台 publication 按 Document.type 精确选择，Kafka 消费者不变；未提供 type 走默认链，未知 type 在 stage 前失败。注册链内全部阶段成功才 apply。普通检索、章节读取共享 SnapshotReader，图检索与 traversal 只在 Neo4j 启用时可用，不绑定读取本体。

三个 Qdrant 集合使用同一个 embedding 模型与维度；普通 dense/sparse，实体和关系 dense-only。启动显式创建/核对集合和 metadata 索引，配置冲突直接失败，不删除或重建已有集合。图集合索引 resource_id/content_revision/chunk_id/ontology_name；普通集合索引 resource_id/content_revision。Mongo 初始化内容、发布与本地 ACL 索引，Neo4j 开启时初始化 schema。

Rerank 始终装配现有 Common RerankClient，以 QWEN_API_KEY/QWEN_RERANKER_MODEL 配置。普通命中用完整 raw_text，图证据用完整种子 ID、来源实体/关系描述和关键词，不裁剪材料。空候选不调用模型，失败由 Common Pipeline 保持原顺序。不存在 ENABLE_RERANK。

`KAFKA_ENABLED=false` 不构造或连接消费者；查询组件和 GC 仍装配。开启后启动 ACL 与发布消费者，使用不同 group；发布的内容/销毁两个主消费者都连接成功才发出 started。存储初始化和全部已启用消费者握手完成后 ready，不等待 GC 首轮，也不发真实模型启动探测。后台循环异常退出撤销 ready 并传播；停止/取消先取消并等待后台任务，再逐个关闭共享底层客户端，部分初始化失败同样清理。共享实例只关闭一次，RerankClient 没有虚构关闭 API。

GC 始终运行，不存在 ENABLE_GC。启动立即开始一轮；本轮完成或扫描失败后等 GC_INTERVAL_SECONDS，再从头按 destroyed/resource_id 游标分页，单进程不重叠。线上 destroy 只建立永久标记、撤销可见性，不等待物理删除。

单资源清理顺序是三个向量集合 → 配置的 Neo4j 来源贡献/孤儿 → 本地 ACL → 全部 Document/Chunk revision。保留 publication 与永久标记，不修改上游资源主库。单资源错误记录阶段并继续后续资源；扫描错误结束本轮，下一轮重新扫描；取消传播。没有本轮重试、清理完成标记、跨库补偿或分布式锁，允许多实例幂等清理。迟到写入由后续轮次再次回收。

GC 的执行错误不会撤销 ready；后台任务自身意外退出仍由生命周期观察。停用后端前须完成其数据回收，不能依赖垂类/算法开关决定是否清理。非销毁资源旧 revision、分块配置变化后的历史回收尚未实现。

外层独立扩展包、provider 覆盖与测试流程见 [extensions.md](extensions.md)。本片不提供新业务 HTTP/MCP 工具入口；health 仅报告实际服务就绪状态。

未来事件可以携带 type 和 metadata，Document 会保存两者，默认分块把 metadata 深复制到各 Chunk。type/metadata 不参与 ContentRevision；影响摄入的变化须提高上游 version，替换路由不自动重建已发布 revision。详细事件示例见 extensions.md。
