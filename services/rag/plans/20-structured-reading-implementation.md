# 20：统一 RAG 章节身份与结构化读取实施记录

记录日期：2026-10-12。切片前规划基线为 RAG **411 passed／17 skipped**。本轮完成统一章节身份、批量章节定位、共享快照授权读取、全局目录、标题邻域与 own/subtree 原文读取。按用户确认，完整移除命中后处理接口；保留工作区已有的 VectorRetriever 单路预算规则并同步测试和 demo。

## 1. 摄入事实与身份

core/schema/content.py 的 rag_section_id 沿用旧服务：

```python
identity = f"{resource_id}\0{content_revision}\0{common_section_id}"
section_id = f"rsec_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"
```

DocumentReadyEvent.to_document 复用 Common 分块结果，只重写章节 ID 与父章节 ID；章节树、Section/Anchor 类型、路径、顺序、原文范围、摘要与 preview 保留。RAG MarkdownChunker 共用身份函数，Chunk.section_id 和 Document 一致，ChunkHit 原样传递。因此 OutlineFormatter 输出的 ID 可以直接用于邻域或正文读取，不需要调用方传 resource_id/content_revision，也不解析 hash。

Chunk.chunk_id 和向量 record_id 保持原合同。新摄入及相关测试装配统一使用章节 hash；不支持旧章节 ID 兼容或自动数据迁移。本轮未连接或修改现有数据库；已有开发事实需独立重建，已 applied 同身份发布仍遵守跳过规则。

## 2. Storage 与共享快照

ContentReader 新增 get_documents_by_section_ids(section_ids) -> list[Document]。Mongo 使用 structure.sections.section_id 的 $in 查询，一次批量读取，再按首个匹配章节的请求顺序恢复文档去重结果。空输入无 I/O，缺失省略，查询及解码异常传播。ensure_indexes 显式增加 document_section_id 普通索引，普通 CRUD 不隐式初始化，也不新增集合。

ResourceSnapshotReader 新增 read_documents_by_section_ids(section_ids, *, snapshot)。定位正文只在 Reader 内部使用，随后复用逐资源首次版本/授权解析与完整正文身份校验。selected revision、document_version、正文 SHA-256 不符或不可见时省略，不换读新版、不回退、不重查已经决定的 publication/ACL。Document 与按章节 Document 读取共用同一正文校验函数；没有独立 resolver 或正文缓存。

## 3. Application 合同

DocumentReading 只注入 ResourceSnapshotReader，方法为：

```python
global_outline(resource_id, *, snapshot, max_level=0) -> DocumentOutline | None
neighborhood(section_ids, *, snapshot, sibling_steps=1) -> list[SectionOutline]
read_sections(section_ids, *, snapshot, scope=SectionReadScope.OWN) -> list[SectionText]
```

目录和邻域直接委托 Common OutlineFormatter，保留标题深度、rsec_ ID、原文偏移、锚点、摘要/preview 和 [C] 标记。max_level=0 为完整目录，sibling_steps=1 为左右各一个兄弟。输入去重保序，局部 formatter/章节索引仅在调用内复用。

结果字段：

| 模型 | 字段 |
| --- | --- |
| DocumentOutline | resource_id、content_revision、outline |
| SectionOutline | resource_id、content_revision、section_id、section_path、outline |
| SectionText | resource_id、content_revision、section_id、section_path、text |

section_path 保持 list[str]。SectionReadScope 为 own/subtree，分别使用 own_span/subtree_span 的半开 Python 字符区间，完整切片 Document.text；保留标题、空白和 Markdown，不拼接 Chunk、不重建标题，不提供窗口、预算、续读或递归深度。

范围超出完整正文时抛 RuntimeError，避免 Python 静默截断；负数/倒序由 Common SourceSpan 在解码时拒绝，解码异常直接传播。合法零长度范围返回空 text。无权、旧版本、缺失项省略；全局目录不可见返回 None，可见但无章节返回空目录。非法 scope、负目录/邻域参数在 I/O 前失败。

## 4. 装配与实际验证

[demo/hybrid.md](../demo/hybrid.md) 已加入检索命中 → 邻域 → own/subtree → 全局目录示例，同次操作显式共享 snapshot。只使用检索返回的 section_id；读取整份文档目录时才传 resource_id。

真实 adapter 装配测试 test_hybrid_composition.py 的 off/high 两种组合都已扩展到章节读取。它使用实际 Common EmbeddingClient、Mongo/Qdrant adapter、SnapshotReader、VectorRetriever、Pipeline 和 ModelReranker，collection/网络/模型响应由测试边界替身提供；目录和 ChunkHit ID 相同，后续章节读取没有增加第二次 ACL 查询。

| 检查 | 实际结果 |
| --- | --- |
| 结构化读取、Mongo 章节定位、摄入适配、事件定向 | **84 passed** |
| 读取/发布/快照/检索/装配联合定向 | **236 passed、3 skipped** |
| Common OutlineFormatter | **5 passed** |
| Chat 缓存章节读取参考回归 | **6 passed** |
| RAG 全服务 | **455 passed、17 skipped** |
| Ruff check、修改范围 format --check | 通过 |
| src/rag 与 tests compileall | 通过 |
| demo 代码块语法/构造合同、Markdown 空白、git diff --check | 通过 |

在 services/rag 内运行的联合定向：

```powershell
uv run pytest tests/application/test_structured_reading.py tests/backends/mongo/test_section_lookup.py tests/ingestion/test_markdown.py tests/application/publication tests/application/test_snapshot_reading.py tests/application/test_snapshot_selection.py tests/application/test_retrieval.py tests/retrieval tests/backends/qdrant/test_hybrid_composition.py -q
uv run pytest -q
```

Common 目录回归从仓库根目录运行：

```powershell
uv run --project services/rag pytest services/wisepen-common/tests/markdown/test_outline_formatter.py -q
```

Chat 参考测试在 services/wisepen-chat-service 内运行：

```powershell
uv run --with pytest --with pytest-asyncio pytest tests/application/tools/output_cache/test_cache_store.py tests/application/tools/output_cache/test_inspect_structure.py -q
```

真实 Mongo 既有隔离测试增加章节查询与索引断言，但本环境未配置隔离连接。17 个跳过项仍为 11 Mongo、3 Kafka、3 Qdrant；未据此宣称真实后端联调完成。

## 5. 收尾与下一步

用户确认移除 HybridRetrieval 的 postprocess_hits 接口及相关测试，检索排序组装后直接返回 ChunkHit。父块暂缓，不保留 ParentBuilder、空 parents 字段或后处理扩展。

保留用户在本轮前调整的预算合同：dense/sparse 单路只用 candidate_limit 作为仓储 top_k，拒绝 dense_top_k/sparse_top_k；hybrid 两路未传预算时各为 30，再以 candidate_limit 截取融合结果。同步单路测试与 demo，并增加错误预算参数在 I/O 前失败的覆盖。

下一切片为 Contextualizer 上下文增强，图谱按既定排期推进；不建立检索效果基线，生产 Container/main、模型/连接配置与生命周期装配继续留后。设计、章节身份、版本读取与交接文档已同步。本轮未提交或推送，既有子模块状态保留。
