# 公司内部合同示例与 metadata 增量投影实施记录

日期：2026-10-13。按用户要求将 Employment 示例改为某公司内部合同，保留用户已改的部门语义和删除员工名称规范化函数的调整。样例、图工具、专用索引、API、文档与测试同步。

## 1. 场景与图事实

[ontology.yaml](../src/rag/extensions/employment/ontology.yaml) 以 employment 为 domain，声明 Person、Department 和有向
in_department。将类别/谓词键同步为 Department/in_department，避免旧 Organization/works_at 键与已经修改的 spec
值不一致。部门关系只来自正文，不从上游业务字段推断。

四份 [contracts.jsonl](../src/rag/extensions/employment/contracts.jsonl) 均为北辰科技内部合同：李明首次入职研发部、之后再次入职产品部；陈晨和王小雨入职研发部。每份上游
metadata 只有 employee/start_date，没有 employment 包装层、employer、部门字段或其他业务字段。单份事件 JSON 同步。

search_graph/follow 保留 ontology_name=employment 条件，关系参数为 in_department；从员工 outgoing 找部门，再从部门 incoming
找员工。读取仍不绑定 Ontology，不新增证据转换或重复授权。

## 2. 原样 metadata 与只读校验

DocumentReadyEvent.to_document 及 Mongo 现有完整替换合同已原样保存 metadata，MarkdownChunker 已逐 Chunk 深拷贝，均无需改动。

[EmploymentMetadataTransformer](../src/rag/extensions/employment/metadata.py) 只读校验 Document 中的
employee/start_date，然后返回原列表。不再逐块 model_dump 或覆盖原业务字段；空文档也检查输入。DTO 忽略非业务字段，以允许后续图字段存在于
Chunk 中，不删改它们。DTO 的日期解析/首尾空白处理只在运行时校验值中发生，不写回 Document/Chunk。

employment_projection 从当前 Chunk 验证业务字段，只返回 year 与校验后的 employee。year 由 start_date 派生，仅存于向量
payload；用户删除的大小写/内部空白规范化函数不恢复。工具传 employee 做精确筛选，API 沿用既有 DTO 的首尾空白处理；没有业务别名解析。

## 3. 通用 VectorIndexer 的增量合同

[VectorIndexer](../src/rag/ingestion/indexers/vector.py) 将 metadata_factory 定义为业务增量，合并 Chunk 的
resource_id/content_revision。业务无需重复声明来源身份，即便增量同名字段冲突，最终身份仍来自 Chunk。不把全部 Chunk metadata
自动投影到向量库，保留普通默认索引只提供定位字段的行为。

content_revision 必须保留在 payload：VectorRetrieval 准备阶段核对向量候选与当前授权 Chunk 的
revision，错误或缺失定位不能成为命中。数据库字段索引是独立选择；本工具不按 revision 过滤，Employment VectorCollection 不再声明
content_revision 的 Qdrant 字段索引。当前声明仅 resource_id KEYWORD、year INTEGER、employee KEYWORD；payload 仍有四个字段。

此改动只更新索引声明，不自动删除已存在集合的旧字段索引，不连接或迁移业务库。资源清理和查询授权仍沿用通用平台。

## 4. 工具、API 与验证

合同工具和 ContractSearch 将 employer 改为 employee，仍只映射年份和员工参数到 MetadataFilters，再调用通用 dense
VectorRetrieval。返回完整 ChunkHit，业务值直接取 hit.metadata.employee/start_date；部门信息通过正文图事实和实体种子继续读取。

最终定向命令：

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests/application/test_employment_extension.py services/rag/tests/ingestion/test_vector_indexer.py services/rag/tests/ingestion/test_markdown.py services/rag/tests/test_extensions.py services/rag/tests/backends/qdrant/test_employment_integration.py --basetemp=.pytest_cache/employment-department-target-final -q --tb=short
```

**59 passed / 1 skipped**。覆盖事件/Document/Mongo/Chunk 业务值往返、原始空白值保留、只读校验与分块隔离、空文档校验、factory
增量及身份保护、revision 实际核验、版本提高后重建、部门三模式与两次一跳、授权、员工查询、API/OpenAPI、共享 GC
和扩展声明。文档代码及链接校验包含在内。

```powershell
.venv/Scripts/python.exe -m pytest services/rag/tests --basetemp=.pytest_cache/employment-department-rag -q --tb=short
.venv/Scripts/python.exe -m pytest services/wisepen-common/tests/retrieval services/wisepen-common/tests/ai_clients services/wisepen-common/tests/markdown --basetemp=.pytest_cache/employment-department-common -q --tb=short
```

RAG **937 passed / 29 skipped**；Common **125 passed**。本次涉及的 11 个 Python 文件 Ruff check / format --check 与
compileall 通过，git diff --check 通过。真实后端仅按显式隔离配置启用，本次缺少配置而跳过；不启动业务消费者、Nacos
入口、真实模型或业务清理/repair。Common/Chat、依赖和既有子模块状态保留。

同步更新扩展指南、Employment README、平台 demo、设计、排期和交接。28/29/30 为当时实施记录，Employment 旧机构场景及旧 metadata
结构以本片为准；通用多本体筛选、来源授权和默认完整发布合同保持。
