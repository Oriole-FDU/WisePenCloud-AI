# P8 首片实施记录：通用装配、资源 GC 与垂类开发指南

日期：2026-10-13。开始基线为 `f7d76b21`（P5 图检索/traversal 已提交）；本片改动尚未提交。保留既有 WisePenView、LightRAG 子模块状态，不修改 Common/Chat 源码或依赖锁文件。

## 实现范围

- `container.py` 声明共享 Mongo、Qdrant、可选 Neo4j、embedding、instruction、固定 Qwen rerank、内容/发布/ACL/SnapshotReader、三个向量存储与所有通用用例。默认实例由 main 注入 settings，独立实例和 provider 覆盖支持垂类组合。
- `app_settings.py` 同时定义 AppSettings 和默认 Nacos settings；没有单独 models.py。`main.py` 的单个 lifespan 管理初始化/后台任务/关闭，没有 runtime.py 或额外应用工厂，参考 Chat/Sandbox 的现有入口习惯。配置 package 不额外自动导出实例，main 直接导入 app_settings.settings。
- 默认 Pipeline 为 MarkdownChunker → [Contextualizer] → ChunkFactsIndexer → VectorIndexer。ENABLE_CONTEXTUALIZE=false，仅控制增强。事实/普通向量必做，图事实不自动生成。GraphExtractor/GraphIndexer 工厂显式传 Ontology 及原组件参数覆盖，读取应用不绑定本体。
- 普通与图检索各自 ModelReranker 共享 Qwen RerankClient，配置必须提供 QWEN_API_KEY/QWEN_RERANKER_MODEL；材料不裁剪，空候选免调用，失败沿用 Common 原顺序降级。无 ENABLE_RERANK，SDK 无关闭接口。
- 显式初始化 Mongo 内容/发布/ACL 索引、三个 collection 与 metadata 索引、可选 Neo4j schema。固定 embedding 模型/维度共享；普通 dense/sparse，两个图集合 dense-only。
- KAFKA_ENABLED 保留；开启时 ACL/发布独立 group，发布 run(started=...) 等待内容/销毁两个主消费者都连接。存储及所有启用消费者就绪后置 ready，不等待 GC 首轮或发真实模型请求。后台意外退出撤销就绪并传播。
- 生命周期注册每个已创建底层客户端的关闭回调，去重关闭；部分初始化失败同样清理。TaskGroup 取消并等待任务后才释放连接。后台包装接收 run callable，业务协程在任务真正开始时才创建，避免启动后立即取消的协程泄漏。

## GC 合同

新增 `application/gc.py` ResourceGarbageCollector.collect/run 与 GcReport(scanned/cleaned/errors)。GC 始终运行，不存在 ENABLE_GC；默认间隔 600 秒，分页大小 100，首轮立即执行、完成后等间隔，单进程无重叠。

PublicationStore/Mongo 增加 list_destroyed_resource_ids(after_resource_id=None,limit=100)，使用 destroyed/resource_id 复合索引与升序 keyset，不用 offset。每轮从头扫描，永久标记/发布状态记录不删除。

逐资源按明确 resource_id 清理普通/实体/关系向量、已配置 Neo4j 来源贡献与受影响孤儿、本地 ACL、全部 Document/Chunk revision。没有上游主库写入。单资源失败记资源与阶段、保留已完成删除继续其他资源；扫描失败由周期循环记录，等下一轮；取消传播。无本轮重试、完成状态、跨库补偿、分布式锁或 leader。

GC 的执行错误不撤销 ready；循环意外退出由生命周期观察。重复扫描回收迟到内容/ACL，也允许多实例幂等重复删除；不承诺一次清理永远无残留。停用后端前应回收其数据，不能靠垂类/算法开关注销 GC 目标。

## 扩展开发材料

新增 demo/platform.md、platform-settings.yaml 和 extensions.md，同步 hybrid/contextual/graph demo、设计 11、排期 10 和交接 16。指南提供独立包目录、真实子容器/Pipeline/发布 provider 覆盖、外层入口、依赖替身测试；不提供插件 Runtime/Registry、自动发现、tag 路由、统一 Ontology 目录或虚构宿主 API。

声明图附加阶段与普通 stage/apply 的独立失败语义。业务 metadata 先进入权威事实，再显式投影并核对；年代示例说明默认 Indexer 不自动索引 arbitrary 字段，当前 GraphRetrieval 不接受年份条件。垂类依赖平台连接/GC；新后端由平台接入清理，不把数据库删除藏在插件里。

## 实际验证

按定向 → 服务 → Common 的顺序执行：

| 范围 | 结果 |
| --- | --- |
| Container、GC、lifespan、扩展示例、发布握手和 Mongo publication 定向 | **117 passed** |
| RAG 全服务 | **861 passed / 28 skipped** |
| Common retrieval 与 ai_clients | **51 passed** |
| 本轮 21 个 Python 文件 Ruff check / format | 通过 |
| 修改文件 compileall 与 git diff --check | 通过 |

定向命令：`.venv/Scripts/python.exe -m pytest services/rag/tests/application/test_gc.py services/rag/tests/application/test_lifespan.py services/rag/tests/test_extensions.py services/rag/tests/test_container.py services/rag/tests/application/publication/test_consumer.py services/rag/tests/backends/mongo/test_publication.py -q --basetemp=.pytest_cache/p8-focused-final`。

全服务命令：`.venv/Scripts/python.exe -m pytest services/rag/tests -q --basetemp=.pytest_cache/p8-full-final`。Common 使用 `--asyncio-mode=auto` 与工作区 basetemp。Windows 格式化通过 Ruff stdin 写回，检查只覆盖修改文件，未顺带修复旧 contextualizer 的既有导入排序问题。

覆盖配置完整性、模型共享、必要索引/上下文开关、本体工厂、固定重排材料/空候选/降级；Kafka 关闭、全部消费者握手、正常/异常退出、初始化/Neo4j 各阶段失败、取消及共享关闭；GC 游标/空扫描/活跃资源隔离、多 revision、重复与迟到写入、阶段失败后恢复、跨资源继续、扫描错误与取消；示例注入/覆盖实际执行。

新增真实 Mongo GC（索引/游标、内容多 revision、本地 ACL、永久标记）、Neo4j GC（删除来源保留其他贡献、最终孤儿）、三个 Qdrant 集合 GC（按 resource_id 跨 revision 删除与活跃资源隔离）测试，均复用本地已存在的显式隔离 fixture。这三项与既有真实后端测试因未配置 RAG_TEST_* 跳过；总 skipped 从 25 增至 28。替身验证不证明实际服务器 Cypher/BM25/网络行为，未连接业务库或 broker，未运行真实模型。

## 后续边界

当前通用平台已装配，部署还需填齐实际 Nacos 配置并在隔离环境完成后端/模型联调。本片不新增业务 HTTP/MCP transport、不回收未 destroyed 的旧 revision/历史分块贡献、不实现在线重构建、动态路由或效果基线。mix 排期待定。后续按实际消费者确定生命周期、图附加阶段调度与工具适配范围，不重复重写已完成组件。
