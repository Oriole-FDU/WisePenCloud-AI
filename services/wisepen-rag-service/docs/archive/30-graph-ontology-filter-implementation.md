# 图本体筛选与 Employment 图工具收敛实施记录

日期：2026-10-13。补齐通用图用例的 ontology_name 条件链路，删除 Employment 授权后再次加载和转换证据的逻辑。本片不绑定查询侧 Ontology，不新增业务查询框架或存储集合。

## 1. 投影和当前事实

[GraphIndexer](../src/rag/ingestion/indexers/graph.py) 在来源 metadata 中保存 resource_id/content_revision/ontology_name，名称取显式绑定的 Ontology.domain，空事实来源也携带它。图向量 payload 原本已有 ontology_name，无需改变记录 ID、向量文本或索引声明。Neo4j 现有参数化平面属性编译器直接处理该属性，不新增本体专用 Cypher 或校验层。

[GraphEvidenceLoader](../src/rag/application/graph/evidence.py) 的当前事实核对支持 resource_id/content_revision/ontology_name 的字符串 EQ、字符串列表 IN 与递归 AND/OR/NOT。向量召回、节点/关系读取和一跳读取接收同一条件，存储在候选预算前执行筛选。授权回查后，ontology_name 从当前 GraphNode 读取，不信任旧投影或业务 metadata，不解析逻辑 ID。保留匹配实体及拥有两个匹配同来源端点的关系。

读取依旧使用通用 parse_graph_facts，不加载本体配置；未提供 filters 时允许读取多个已写入本体。底层负责表达式结构和类型校验；应用继续负责来源授权、当前 revision 和事实过期核对。两个图列表均缺失是无图事实，结构损坏与来源错误仍传播。关系方向、谓词身份、独立来源描述及完整原文合同不变。

## 2. Employment 工具

[EmploymentTools](../src/rag/extensions/employment/tools.py) 的 search_graph 和 follow 只传 ontology_name=employment 条件及各自检索/遍历参数，直接返回 GraphHit/GraphEvidence。删除 _employment_evidence、额外 SnapshotReader 依赖、读取侧 EmploymentMetadata 校验、种子必须存在关系的条件和证据组重建。子容器同步移除该工具的 snapshot_reader 注入；通用用例仍管理自己的授权依赖。

本体条件选择事实命名空间，不保证来源带有 employment 业务字段，也不排除共享该本体的其他可见资料；需要限定资源时组合资源/revision 条件。业务 DTO 留在写入边界。合法实体没有可见关系仍返回，relationships 为空。工具不额外读取或补召回，也不扩张 global 关系组。普通合同检索和 API 返回合同保持。

## 3. 旧投影维护

旧 Neo4j 来源未保存 ontology_name，不会匹配该字段的 EQ/IN 条件。按维护窗口约定，使用 repair_graph 的 --projection-only --with-graph、显式资源列表与对应 Ontology 文件重建来源属性；不抽取、不保存 Chunk，保留发布状态复核。图向量本来已有字段，此次不要求重建向量。没有自动迁移、启动全库扫描或业务数据操作。

## 4. 实际验证

先运行定向：

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests/application/graph services/rag/tests/application/test_employment_extension.py services/rag/tests/ingestion/test_graph_indexer.py services/rag/tests/ingestion/test_graph_vector_indexer.py services/rag/tests/backends/neo4j services/rag/tests/scripts/test_repair_graph.py --basetemp=.pytest_cache/ontology-filter-target -q --tb=short
```

**209 passed / 4 skipped**。覆盖来源投影字段、合法空投影、三种查询路线、EQ/IN 与组合条件、当前事实与业务字段区分、多本体读取、旧投影不能确认种子、来源/端点范围及授权、无关系实体保留和工具无重复回查。随后补强已有 Neo4j 替身测试，验证其他本体不占逻辑关系预算；该补强由以下全量回归验证。

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests --basetemp=.pytest_cache/ontology-filter-rag -q --tb=short
.venv/Scripts/python.exe -m pytest services/wisepen-common/tests/retrieval services/wisepen-common/tests/ai_clients services/wisepen-common/tests/markdown --basetemp=.pytest_cache/ontology-filter-common -q --tb=short
```

RAG **932 passed / 29 skipped**；Common **125 passed**。文档代码编译和本地链接检查包含在 RAG 回归。真实 Neo4j fixture 新增本体条件读回断言，但本次缺少显式测试配置而跳过；其他真实后端同样仅按隔离 fixture 配置运行，当前跳过。不启动业务消费者、Nacos 入口、真实模型、业务 repair 或清理业务数据库。

本片修改的 11 个 Python 文件 Ruff check / format --check、compileall 和 git diff --check 通过。同步更新扩展指南、Employment README、graph demo、设计、排期及交接。29 中“图条件仅来源字段”和“业务来源选择保持”是当时范围，以本片为准。Common/Chat 源码、依赖及既有子模块状态保留。
