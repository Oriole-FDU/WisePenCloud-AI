# RAG V4 Schema 与 Ingestion 实施记录

## 已完成范围

本阶段实现位于 `services/rag/src/rag`，以本次 Schema 与 Ingestion 方案为准。

- `core/schema/document.py`：Document、DocumentStructure 使用 dataclass，直接复用 Common Section 和 Anchor；正文只有 `raw_markdown`。
- `core/schema/graph.py`：GraphNode、GraphEdge 使用简单 Pydantic 模型，仅包含方案指定字段。节点名称只进行首尾空白清理、casefold 和连续空白归一；缺失 category 时 ID 保留空槽。边 ID 保持方向和原始 relation_type。
- `ingestion/models.py`：Chunk 使用 `dataclass(slots=True)`，保留指定的十个字段；`retrieval_text` 只从普通 metadata 的 `contextual_context` 组装。
- `ingestion/chunkers/base.py`、`transformers/base.py`、`indexers/base.py`：分别定义三个 Protocol；Transformer 可接收当前 Document。
- `ingestion/chunkers/markdown.py`：调用 Common `.chunk(document.raw_markdown)`，适配正文、顺序、token 数、section path 和 anchor labels；不复制内部 spans、节点、overflow 或 content hash。
- `ingestion/pipeline.py`：Chunker 执行一次，Transformer 依次传递返回列表，Indexer 依次串行消费最终列表；异常原样传播，不重试、不回滚、不复制 Chunk。
- `tests/core/test_schema.py`、`tests/ingestion/test_pipeline.py`、`tests/ingestion/test_markdown.py`：共 30 个测试用例覆盖模型合同、顺序、对象 identity、metadata 增强、新列表交接、逐阶段失败和真实 Common 分块适配。

RAG Chunk 和 section 的本地 ID 格式为：

```text
{resource_id}:{content_revision}:{Common ID}
```

DocumentStructure 保留 Common 原始结构，不由 Chunker 重写；Chunk.section_id 在适配时加上资源与 revision 前缀。document_version 不参与 Chunk identity。

## 验证结果

从仓库根目录执行：

| 命令 | 结果 |
| --- | --- |
| `uv run --project services/rag pytest -q services/rag/tests/core services/rag/tests/ingestion` | 30 passed |
| `uv run --project services/rag pytest -q services/rag/tests/core services/rag/tests/ingestion services/rag/tests/acl` | 34 passed，1 failed，失败来自既有 ACL 测试 |
| `uv run --project services/rag pytest -q services/rag/tests/acl` | 4 passed，1 failed，独立复现同一既有失败 |
| `uv run --project services/rag ruff check services/rag/src/rag services/rag/tests` | 通过 |
| `uv run --project services/rag python -m compileall -q services/rag/src/rag` | 通过 |
| `git diff --check` | 通过 |

既有失败为 `tests/acl/test_synchronizer.py::test_authoritative_acl_is_projected_and_saved`：ResourceAcl 模型和 synchronizer 使用 list，测试构造的期望 ResourceAcl 使用 tuple，导致 dataclass 相等性断言失败。已核对当前 HEAD 中的模型与测试，并通过独立 ACL 测试复现；本阶段未修改 ACL 源码或测试。

## 后续边界

本阶段未实现 GraphExtractor、VectorIndexer、GraphIndexer、Storage、Retriever 或 application 调用方；未修改 ACL、publication、旧 `services/wisepen-rag-service` 或 Common Markdown 算法。

后续组件从各自模块的 Protocol 扩展，继续遵守普通 metadata、只读 Indexer、串行失败即停的合同。持久化、真实后端、LLM 抽取及应用发布行为留待对应阶段验证。
