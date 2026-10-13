# RAG V4 Schema 与 Ingestion 实施记录

## 已完成范围

实现位于 `services/rag/src/rag`。本记录按当前模型归属更新，内容存储扩展与完整验证见 13 实施记录。

- `core/schema/content.py`：Document、DocumentStructure、Chunk 使用独立 dataclass，共同作为 ingestion 和 Storage 的框架合同；直接复用 Common Section 和 Anchor，当前文档正文字段为 `text`。后续 publication 阶段增加不可变 ContentRevision，见 14。
- `core/schema/graph.py`：GraphNode、GraphEdge 使用简单 Pydantic 模型，仅包含方案指定字段。节点名称只进行首尾空白清理、casefold 和连续空白归一；缺失 category 时 ID 保留空槽。边 ID 保持方向和原始 relation_type。
- Chunk 保留指定的十个字段；`retrieval_text` 只从普通 metadata 的 `contextual_context` 组装。Chunker 接口不再定义模型，旧 document 模块和 ingestion 的 Chunk 导出已移除。
- `ingestion/chunkers/base.py`、`transformers/base.py`、`indexers/base.py`：分别定义三个 Protocol；Transformer 可接收当前 Document。
- `ingestion/chunkers/markdown.py`：调用 Common `.chunk(document.text)`，适配正文、顺序、token 数、section path 和 anchor labels；不复制内部 spans、节点、overflow 或 content hash。
- `ingestion/pipeline.py`：Chunker 执行一次，Transformer 依次传递返回列表，Indexer 依次串行消费最终列表；全部成功后返回最终同一 `list[Chunk]`。异常原样传播，不重试、不回滚、不复制 Chunk，不增加装配校验。
- `tests/core/test_schema.py`、`tests/ingestion/test_pipeline.py`、`tests/ingestion/test_markdown.py`：覆盖模型合同、归属、顺序、对象 identity、metadata 增强、新列表交接、逐阶段失败和真实 Common 分块适配。

RAG Chunk ID 保持原格式：

```text
{resource_id}:{content_revision}:{Common ID}
```

章节身份已在后续结构化读取切片统一，见 [20 实施记录](20-structured-reading-implementation.md)。Document 构造与 Chunker 共用 `core/schema/content.py` 的 rag_section_id，使用 `rsec_ + sha256(resource_id + "\0" + content_revision + "\0" + common_section_id)[:24]`。Document 章节及父章节 ID、Chunk.section_id 和 ChunkHit.section_id 一致；Common Section 类型、树、原文范围、路径、摘要、preview 和 anchors 保留。Chunk ID 格式不变，document_version 不直接参与 Chunk identity（内容版本已由 revision 限定）。

## 验证结果

从仓库根目录执行：

| 命令 | 结果 |
| --- | --- |
| `uv run --project services/rag pytest -q services/rag/tests` | 63 passed，3 skipped；跳过项为未配置连接的真实 Mongo 测试 |
| `uv run --project services/rag ruff check services/rag/src/rag services/rag/tests` | 通过 |
| `uv run --project services/rag python -m compileall -q services/rag/src/rag` | 通过 |
| `git diff --check` | 通过 |

当前 ACL 测试通过；本次内容模型与存储实现未修改 ACL 源码或测试。

## 后续边界

当前已扩展 ContentStorage、MongoContentStorage 和 ChunkFactsIndexer，详见 13。application.publication 的身份、摄入、事实回查、条件发布和永久销毁标记见 14。GraphExtractor、真实 VectorIndexer、GraphIndexer、Retriever 和事件消费仍未实现。

后续组件从各自模块的 Protocol 扩展，继续遵守普通 metadata、只读 Indexer、串行失败即停和开发者正确装配的合同。表中的 63/3 是内容存储阶段结果，当前结果见 14；真实 Mongo、向量与图后端、LLM 抽取和消息确认留待对应阶段验证。
