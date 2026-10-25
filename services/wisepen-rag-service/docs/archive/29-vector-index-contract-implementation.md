# 向量索引协议统一与 Employment 检索收敛实施记录

日期：2026-10-13。按用户确认计划统一后端无关索引声明、集合初始化与通用正文三模式查询；ChunkHit 暴露完整当前
metadata，Employment 合同工具仅做业务参数映射和用例编排。

## 1. 统一索引协议与启动

[storage/vector.py](../src/rag/storage/vector.py) 新增 IndexKind (KEYWORD/INTEGER/FLOAT/BOOLEAN)、冻结轻量 IndexableField
(name, kind)、VectorCollection (storage, metadata_index_fields)。VectorStorage 组合既有读写协议，要求 collection 属性和必填
list[IndexableField] 的 ensure_collection。无无参/字符串列表兼容接口，不提供 DATETIME；业务需要时间字段时自行转换 Unix 时间戳。

Qdrant 移除构造器 metadata_indexes，内部 KIND_MAP 将声明翻译成 SDK，BOOLEAN 映射 BOOL。空字段列表仍初始化/核对向量配置，不创建
payload 索引；平面字段约束、dense/sparse 配置、wait=True 和 COMPLETED 确认保留，不增加自动重试或补偿。

Container 和 Extension 以 VectorCollection 声明集合；main lifespan 统一遍历平台 vector_collections 并显式初始化，不写后端专用分支或
hasattr。默认普通集合只声明 resource_id，通用图集合声明 resource_id/chunk_id/ontology_name；Employment 明确
resource_id/content_revision KEYWORD、year INTEGER、employer KEYWORD。

登记扩展同时追加集合配置与共享 GC writer 列表，GC 仍只依赖 Writer，不解释字段声明。collector 先构造后登记扩展仍可清理新存储。缺少
ensure_collection 的 writer 在启动调用时自然失败，所有已创建客户端清理。repair_context/repair_graph 与隔离 fixture
全部迁移到新声明，脚本自身已有字段选择保留。

## 2. 通用正文用例与 metadata

[application/retrieval.py](../src/rag/application/retrieval.py) 的公开类改为 VectorRetrieval，Container provider 为
vector_retrieval，代码/当前示例同步，不保留旧名别名。retrieve 增加 mode，默认 HYBRID，支持
DENSE/SPARSE；dense_top_k/sparse_top_k 默认为 None，让 Retriever 维持 hybrid 默认每路 30 条及单路 candidate_limit 合同。单路拒绝
hybrid 路内预算。

ChunkHit 在既有默认字段后新增 metadata 默认空字典，直接使用当前已授权 Chunk 的完整字典。正文仍为 raw_text，metadata 不来自向量
payload、不设置白名单，也不额外复制/解释垂类 schema。向量和内部排序 payload 不进入返回。

通用准备继续核对 SnapshotReader 授权、selected revision 和向量记录/资源身份；filters 由 VectorReader
执行。保留普通检索原来的候选预算行为，candidate_limit 小于 top_k 可返回较少结果，没有新增全局限制。重排/MMR 和实体候选既有行为保持。

## 3. Employment 工具与 API

[EmploymentTools.search_contracts](../src/rag/extensions/employment/tools.py) 保留业务年份范围/机构名称及工具预算约束，将
GTE/LTE/EQ 组合为 MetadataFilters，再调用专用集合绑定的通用 VectorRetrieval (mode=DENSE)。不实现
Retriever/Pipeline/prepare，不重复读取或重新筛选业务条件。

删除 employment/retrieval.py、PreparedEmployment、EmploymentRetrieval、EmploymentHit。合同 API 直接返回 list[ChunkHit]
，业务字段来自 hit.metadata.employment；OpenAPI 同步。写入 DTO、Transformer、metadata_factory 保留，专用向量 payload 仅
resource_id/content_revision/year/employer，移除初版业务及文本摘要。

使用既有版本合同：影响摄入的业务 metadata 变化须提高 version，同一已发布 revision 跳过构建。VectorReader 必须执行
filters，通用正文不额外保证同 revision 被直接改写业务字段后的投影新鲜度，也不在读取时解析业务
schema。相关旧测试改为验证新版本和存储过滤职责；图查询事实/来源/摘要核验及业务来源选择保持。

## 4. 实际验证

先执行定向：

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests/application/test_retrieval.py services/rag/tests/application/test_employment_extension.py services/rag/tests/application/test_lifespan.py services/rag/tests/test_container.py services/rag/tests/test_extensions.py services/rag/tests/backends/qdrant services/rag/tests/scripts --basetemp=.pytest_cache/vector-contract-target-final -q --tb=short
```

**247 passed / 9 skipped**。覆盖四类型映射、空字段/重复初始化/配置冲突/写入失败、缺少协议方法的启动失败和清理、默认及扩展配置/共享
GC、三模式预算及模型调用数量、metadata 权威值、隐藏来源/旧 revision、业务条件的存储过滤、新版本变更、API/OpenAPI 与文档代码。

随后执行：

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests --basetemp=.pytest_cache/vector-contract-rag-final -q --tb=short
.venv/Scripts/python.exe -m pytest services/wisepen-common/tests/retrieval services/wisepen-common/tests/ai_clients services/wisepen-common/tests/markdown --basetemp=.pytest_cache/vector-contract-common -q --tb=short
```

RAG **928 passed / 29 skipped**；Common **125 passed**。修改/新增的 **28 个 Python 文件** Ruff check 与 format --check
通过，src/rag 与 scripts 的 compileall 通过，git diff --check 通过。真实 Mongo/Neo4j/Qdrant/Kafka fixture
仍仅按显式测试配置启用并隔离，当前全部缺少配置而跳过。没有启动业务消费者、业务清理/repair、Nacos 入口或真实模型。

## 5. 边界与交接

后续图条件与工具收敛见 [30](30-graph-ontology-filter-implementation.md)：通用图用例已补 ontology_name 筛选，Employment
图工具改为传条件后直接返回结果；下述“图条件仅支持来源字段”及第 3 节“业务来源选择保持”为本片当时范围。

当前指南/README、设计、排期和交接已更新；28 是初版历史记录，以本片合同为准。没有新增 Mongo 集合、Stored 模型、通用 metadata
schema、读取侧业务过滤解释器或插件 Runtime；图条件仍仅支持原有来源字段。Common/Chat 源码、依赖/锁文件及既有子模块状态保留。
