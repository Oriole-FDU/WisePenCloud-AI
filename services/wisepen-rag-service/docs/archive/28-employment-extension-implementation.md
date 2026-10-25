# Employment 高级扩展与平台存储/API 接入实施记录

日期：2026-10-13。用户要求用大量雇佣合同示例替代 extensions/demo，验证专用索引、通用用例封装和 API 挂载；确认 metadata
是入职日期及雇佣机构的业务权威，正文人员/机构关系仍用图抽取。

## 1. 完成范围

源码包位于 [extensions/employment](../src/rag/extensions/employment/README.md)，包含包内 YAML 本体、四份独立事件、业务
metadata DTO/Transformer、子容器、专用合同检索、图工具以及子 FastAPI。旧 demo
源码被替代；通用开发指南放在 [extensions/extensions.md](../src/rag/extensions/extensions.md)，分别说明基础文件入库/图索引、专用索引及业务工具、API
接入和验证。

type=employment 仍通过平台唯一 DocumentPublication/Kafka 路由；不覆盖 publisher、不接触消费者。完整链保留默认
chunker/上下文/事实/普通向量，追加正文图抽取、GraphIndexer 和专用 VectorIndexer。全部阶段成功才 apply；专用写入失败保留旧
applied 与已完成投影，重跑 staged 版本恢复。

metadata.employment 的 employee/employer/start_date 先经业务边界校验；Chunk 保存规范化值并保留其他 metadata，Document
仍保存事件原始 metadata。year 从日期派生，机构精确匹配规范化大小写和空白，不做别名消歧。图提示材料没有加入业务 metadata。

## 2. 发现的机制缺口与实际调整

原 EXTENSIONS 构造器只交付 pipeline ()，不能让新集合进入初始化/GC，也不能保存和挂载已注入的工具。现在构造器提供 extension
()，返回轻量 [Extension](../src/rag/extensions/base.py)：pipeline、额外 vector_storages、可选 api。没有新增插件
Runtime、自动发现或在线管理 Registry。

[register_extensions](../src/rag/extensions/__init__.py) 返回按名称保留的装配产物，把 Pipeline 写入同一份 routes
字典、额外存储追加到平台共享列表，拒绝未知名称和物理集合冲突。平台 [Container](../src/rag/container.py) 的 vector_storages
是实例级共享 Singleton 列表，GC 保留该引用，先构造 collector 后登记扩展也能覆盖新增清理目标。

[main.lifespan](../src/rag/main.py) 在登记共享客户端后构造扩展，随后初始化额外集合、挂载子应用，所有必需初始化/消费者就绪后同步
ready。停止/取消/构造或初始化失败仍由平台收敛后台任务、关闭已拥有的客户端一次。子应用不声明自己的连接/后台生命周期。默认
EXTENSIONS=[] 不构造示例，不改变默认 Pipeline。

## 3. 专用索引和授权准备

QdrantVectorStorage 构造器新增带类型的 metadata_indexes，保留原 metadata_index_fields 的 keyword 显式覆盖；collection
属性用于物理集合隔离检查。默认普通集合只索引 resource_id，通用图集合初始化
resource_id/chunk_id/ontology_name，保留用户本次已有调整；revision 仍保存在 payload 并核对。

Employment 集合默认 employment_contract_vectors_v4，dense-only，使用平台同一 embedding 模型/维度；resource_id/content_revision
和 employer 为 KEYWORD，year 为 INTEGER。VectorIndexer 增加 metadata_factory 以复用 embedding 响应校验及分批写入；本例不复制一个专用
embedding Indexer。

每 Chunk 一条记录，record_id 沿用 chunk_id，payload 只保存来源定位、year/employer、业务事实和实际 retrieval_text 摘要。没有新
Mongo 集合/Stored 模型。重建只 upsert 当前 Chunk，历史/分块变更残留仍留后。

EmploymentRetrieval 组合公开 VectorRetriever、Common RetrievalPipeline 和 SnapshotReader。年份/机构在向量召回及当前 Chunk
上核对，资源/revision 与两个摘要也核对；授权后才能送重排。过期、隐藏、销毁或缺失来源丢弃，业务结构损坏传播，无合法候选不补召回，空候选不调用模型，重排失败原顺序降级。返回完整
raw_text、业务字段及 seed_entities。

普通正文与业务正文共用公开 chunk_seed_entities，移除了跨模块调用 HybridRetrieval 私有方法的需要；图候选补充失败仍日志并返回空，不撤销正文命中。

## 4. 图工具与 API

EmploymentTools 使用通用 GraphRetrieval 的 local/global/hybrid、GraphTraversal 的一次定向 works_at 以及
DocumentReading。通用读取不绑定 Ontology；业务层按当前 employment 来源及实体 ontology_name 选择证据，不解析
ID。其他业务来源/命名空间即使有 works_at 也不会进入结果。选择在通用候选/排序预算之后，不补召回，不自动多跳。

五个只读接口挂在 /rag/extensions/employment：contracts/search、graph/search、graph/traverse、合同
outline、contracts/sections。使用 Common SecurityHeaderMiddleware 和 FROM_SOURCE_SECRET 校验可信网关，由安全上下文生成每请求
snapshot；正文禁止声明 user_id/ACL。root health 不受该中间件保护。子 API 与宿主同步 ready；无令牌 404、未就绪 503、无可信用户
401，隐藏目录 404。

HTTP 请求 DTO 处理外部参数与业务预算，响应直接引用 application dataclass 生成真实 OpenAPI 合同，不暴露内部向量或
snapshot。未加全局 ValueError 捕获来吞掉事实解析/存储错误。入库仍由平台 Kafka/publication，不开放未定义写权限的 publish
HTTP 接口。

README 展示正常启动、事件导入、年份/机构查询、三模式图工具、从 ChunkHit 选择员工到机构再 incoming 找同事的两次显式一跳，以及在串行维护窗口通过公开
contract_indexer 重建专用向量；示例代码和相对链接均有验证。

## 5. 实际验证

定向（先于最终全服务回归）：

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests/test_extensions.py services/rag/tests/application/test_employment_extension.py services/rag/tests/application/test_lifespan.py services/rag/tests/test_container.py services/rag/tests/backends/qdrant/test_vector.py services/rag/tests/backends/qdrant/test_employment_integration.py services/rag/tests/ingestion --basetemp=.pytest_cache/employment-target-complete -q --tb=short
```

结果 **284 passed / 1 skipped**。覆盖实际四份事件完整发布、四个向量集合及共享图来源、业务字段/文本摘要/身份过期、隐藏/destroyed/旧
revision、三模式和两次一跳、其他业务来源和本体排除、重排前业务准备/空候选/失败降级、API 网关身份与事实异常、专用写入失败恢复、共享
GC、初始化/挂载/构造失败客户端清理、文档代码执行和类型化索引。

全服务与 Common：

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests --basetemp=.pytest_cache/employment-rag-complete -q --tb=short
.venv/Scripts/python.exe -m pytest services/wisepen-common/tests/retrieval services/wisepen-common/tests/ai_clients services/wisepen-common/tests/markdown --basetemp=.pytest_cache/employment-common -q --tb=short
```

RAG **919 passed / 29 skipped**；Common **125 passed**。修改/新增的 **19 个 Python 文件** Ruff check/format --check
通过，src/rag compileall 通过，git diff --check 通过。

新增真实 Qdrant 测试只在 RAG_TEST_QDRANT_URL 配置后使用既有随机隔离 fixture，验证 INTEGER 索引、年份/机构组合条件与单资源删除。当前无显式
Mongo/Neo4j/Qdrant/Kafka 测试配置，真实 fixture 跳过；无真实模型、业务入口、消费者、业务清理或业务 repair 联调。Common/Chat
源码和既有子模块状态未修改。

## 6. 边界与后续

本例是源码登记 + 启动配置 + 实际装配产物，不是动态插件宿主。启用完整 Employment
需要图/指令模型及网关配置；停用前回收额外集合，否则配置不再声明清理目标。year/employer 只进入专用合同集合，不自动扩大通用
GraphRetrieval filters；时间图工具需要自己定义准备/来源范围。专用维护不 stage/apply、不重分块或重写普通向量，不做跨库回滚。

mix、MCP、生产发布/部署、在线安装/卸载、历史贡献全面回收及真实环境效果验证继续留后。

## 后续收敛

本记录描述初版。用户确认的索引协议与工具收敛已在 [29](29-vector-index-contract-implementation.md) 落地：SDK 索引声明改为
IndexKind/IndexableField，Extension 提供 vector_collections，启动统一调用 VectorStorage；专用
EmploymentRetrieval/EmploymentHit 已删除，工具只映射 filters 再调用通用 VectorRetrieval，直接返回带 metadata 的
ChunkHit。业务变更提高 version，初版同 revision 业务/文本摘要保护及其测试不再是当前合同。图事实核验保持。
