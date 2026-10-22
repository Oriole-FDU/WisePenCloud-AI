# WisePen RAG Chunking 重构迁移阻塞项

本文记录 Token Budget、结构身份和无 overlap Chunk 切换后，RAG 哪些路径可以继续运行，哪些路径必须等待后续上下文增强重构。

## 已切换的事实

- Common 使用 `target_chunk_tokens=800`、`split_threshold_tokens=1600`；800 是组合目标，1600 是拆分触发阈值，完整不可拆结构允许超过阈值；
- `DocumentChunker` 编排 Parser、StructuralNodeSplitter 和独立 ChunkPacker；标题只参与章节组织，Overflow 由最终 Chunk 正文的实际 Token 数决定；
- Chunk 不再以 Markdown SourceSpan 作为合法性合同；
- RAG DocChunk 使用 node_ids、section_id、section_path、chunk_index 和 content_token_count；
- Graph 抽取只使用 section_path + target chunk；
- Chat cache 仍保留自己的字符 offset，因为 range 工具公开要求 start_offset/end_offset；
- 没有线上数据库，Mongo/Qdrant 使用新 schema 直接重建。

## 暂不可运行的 RAG 路径

| 模块与路径（相对 `services/wisepen-rag-service/src/rag`） | 调用方 | 新状态 | 后续替代 |
|---|---|---|---|
| `application/document/indexing.py::DocumentIndexBuilder._enhance` | `build_and_publish()` | `enhancement_enabled=True` 明确失败；False 返回原 chunks | 重做 index context 和 prefix budget |
| `application/document/indexing.py::_generate_retrieval_context` | 原 `_enhance()` 的 LLM 分支 | 保留明确迁移错误入口，旧 LLM 分支已移除 | 独立 structural contextualization |
| `application/retrieval/hybrid_retriever.py::_build_dynamic_parents` | `HybridRetriever.retrieve()` | 有相关命中时明确失败 | 使用 chunk identity 和 ContextExpander |
| 原 `_expanded_groups` / `_parent_from_span` | 原 `_build_dynamic_parents()` | span coverage / Markdown slice 实现已删除 | chunk index 覆盖与结构 ContextUnit |
| `application/document/preparation.py::DocumentPreparer.prepare` | 文档 prepare 用例 | identity/token 投影可运行 | 无 Chunk span 合同 |
| `application/graph/graph_fact_builder.py::_extract_chunk` | GraphFactBuilder | target + section path 可运行 | target 保持唯一 evidence |
| `api/endpoints/reading.py` / `application/reading.py` | Section/Anchor/Range 读取 | 保留 Section/Anchor provenance | 后续单独迁移 Section identity 读取 |
| `core/persistence/qdrant/document_vector_repository.py` | indexing / candidate retrieval | 增加 node/order/token payload | Mongo 提供正文，Qdrant 不保存 span |

当 enhancement_enabled=True 时，索引增强会抛出 migration-required 错误，避免静默使用已经删除的 span 语义。临时使用 enhancement_enabled=False 可以生成基础 raw-text 索引，但不产生新的 retrieval_context。

`DOCUMENT_ENHANCEMENT_ENABLED` 现有默认值是 False。`HybridRetriever` 的 HTTP 输出只有 parents，因此有命中时必须报 `ContextMigrationRequired`；不能返回成功但空 parents 的响应。没有候选或被相关性门控拒绝时，原有空结果仍有效。Qdrant 的 Dense/BM25 候选接口可以独立运行。

## 暂时可运行的路径

- DocumentPreparer.prepare()：生成新的 identity/token Chunk；
- Common Parser、Section builder 和 Outline；
- GraphFactBuilder：target chunk 是唯一 evidence，section path 只用于消歧；
- RAG Section read：目前仍使用 Document/Section provenance；
- Qdrant candidate retrieval：候选使用 chunk、section、ACL 和 revision identity；
- Chat cache 的 structure、section、range、regex 和 relevance 工具。

Chat 的 offset 不属于 RAG Chunk。它定位的是 Redis 中缓存正文的字符区间，供范围读取和超预算续读；因此本次只在 Chat adapter 内保留。

Chat 在 `cache_store.py::_build_cache_chunks()` 中直接切分缓存原文的 Section 字符范围。它不反推 Common normalization 后文本的位置，每个 cache chunk 的 text 严格等于原文 `[start_offset:end_offset]`。Redis key 使用 v10，旧 v9 缓存自然失效。

## 后续 Context Expansion 项目

本次已加入未接入普通检索的 `ContextExpander` 骨架，后续项目需要把它接入索引和生成路径。它输入 seed chunks、同 revision 的 ordered chunks、strategy 和 token budget，输出带有 seed/neighbor/section_context 角色的 ContextUnit[]。

最小策略：

~~~text
NONE
NEIGHBOR
SECTION_LOCAL
WHOLE_SECTION_IF_SMALL
~~~

邻居必须满足：

- 同一 resource 和 content revision；
- 同一 Section 或允许的 subtree；
- 按 chunk_index 顺序选择；
- 每个 Chunk 最多出现一次；
- token budget 超限即停止；
- seed evidence 与 background context 分开渲染。

Graph 当前使用 NONE，不把邻居内容错误归因于 target chunk。

当前扩展仅支持精确 section_id 相同的邻居，不从显示标题推断 subtree；支持 subtree 需要后续传入 Section identity 树。预算小于完整 seeds 时明确抛错；WHOLE_SECTION_IF_SMALL 按整 Section 装入或跳过，不输出部分章节。

## 离线 tokenizer 与部署重建

Common 随包提供固定 `cl100k_base.tiktoken` 表（SHA-256 见 `common/utils/markdown/data/README.md`）。冷启动直接构造 tiktoken Encoding，既不下载模型也不下载编码表。`TokenCounter` 可以在 `DocumentChunker(token_counter=...)` 注入；更换 tokenizer 或版本后要重新分块。

没有线上数据库，本次不添加 dual-read、兼容字段或迁移脚本。实际开发部署按以下顺序操作：

1. 关闭旧 enhancement；停止旧 schema 的写入者。
2. 清空开发环境旧 Mongo Document/DocChunk 和对应 revision/index-state 投影，或使用新的数据库命名空间。
3. 清空旧 Qdrant document collection，或配置新的 collection。
4. 从资源的上游 Markdown 重新 prepare/index，确认 staged revision 发布为 active；Graph facts/vectors 也按新 chunk_id 重建。
5. Chat 使用 v10 key，旧缓存等待 TTL 过期。

本次只完成代码/schema 和本地投影验证；没有对运行中的 Mongo/Qdrant/Redis 执行清空或索引重建，也没有调用外部 embedding/Graph LLM。重建必须使用目标开发环境的连接与资源清单。

## 验收边界

本次完成标准是 Common preparation、RAG identity projection、Graph target-only extraction 和 Chat 私有 offset 继续可测试；旧 retrieval context enhancement 与 span dynamic parent 的不可用状态必须显式可诊断。后续项目完成后，才重新开放 enhancement_enabled=True 和 dynamic parent。

聚焦测试覆盖 tokenizer 冷启动、混合 Unicode、目标距离装箱、原始节点归属、代码缩进和完整围栏、表格 header/caption、原子结构 Overflow、Graph prompt/source attribution、Mongo/Qdrant 投影、Chat continuation/regex/relevance range。Chunk 的 node_ids 均指向 Parser 原始树，派生片段允许重复归属同一原始节点；Chat 的原文字符范围切分保持独立。旧 span-inline context 和 dynamic-parent 成功行为不再是有效测试预期；相应入口应验证明确的迁移错误。当前测试并不证明外部 embedding/LLM 或数据库端到端可用。
