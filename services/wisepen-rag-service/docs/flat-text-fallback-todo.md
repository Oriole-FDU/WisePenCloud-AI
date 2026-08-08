# Flat Text Fallback TODO

本清单用于跟踪无标题文本降级、ReadingBlock top-k 修复和图谱跳过能力。
每完成并验证一个修改单元，立即勾选对应项目。

## 1. 投影模式

- [x] 创建实施 TODO 文档。
- [x] 定义 `SECTIONED`、`FLAT_TEXT`、`EMPTY` 投影模式。
- [x] 让内容投影、索引结果和资源快照携带投影模式。
- [x] 在 content revision 中持久化模式，并升级 projection schema version。

## 2. Flat Text 投影

- [x] 使用 Markdown 解析结果识别真实标题、页码和锚点。
- [x] 使用 `PlainTextChunker(6000, overlap=0)` 生成合成 Section 和 ReadingBlock。
- [x] 使用 `PlainTextChunker(800, overlap=100)` 生成 RetrievalChunk。
- [x] 保持 SourceRef、Python 字符 offset、page label 和 anchor label 正确。
- [x] 空内容不生成 Section、ReadingBlock、RetrievalChunk 或 SourceRef。

## 3. 内容索引策略

- [x] `SECTIONED` 保持 contextual indexing。
- [x] `FLAT_TEXT` 跳过 contextual indexing，但保留 embedding、BM25、ACL 和 revision 发布。
- [x] `EMPTY` 跳过 contextual indexing、ACL 加载和 embedding，并清理旧向量 revision。

## 4. ReadingBlock Top-K

- [x] 检索排序返回候选水位内的完整有序 RetrievalChunk。
- [x] 按 `(resource_id, reading_block_id)` 稳定去重后再截取 top-k。
- [x] 同一 ReadingBlock 只保留最高排名 chunk，同一 Section 的不同 ReadingBlock 全部保留。
- [x] Materializer 只负责回源和最终 ACL，不再按 Section 去重。
- [x] Navigator 将同一 Section 的多个命中窗口按排名聚合到一个 SectionView。

## 5. 图谱跳过

- [x] 增加 `SKIPPED`、`ALREADY_SKIPPED` 图谱索引动作。
- [x] 增加非结构化 revision 的图谱跳过和幂等查询仓储契约。
- [x] Neo4j 跳过时清理旧 relations、mentions 和孤立节点，并记录 skipped revision。
- [x] structured -> flat 清理旧图，flat -> structured 恢复正常图谱投影。
- [x] flat/empty 路径不调用 graph extractor 或图谱 LLM。

## 6. 对外契约

- [x] document structure 返回 `structure_mode`。
- [x] flat document structure 返回可直接读取的合成 Section。
- [x] MCP `rag_get_document_structure` 透传 `structure_mode`。
- [x] MCP `max_results` 描述明确为 ReadingBlock 窗口数量。

## 7. 测试

- [x] 重写旧的 Section 级 evidence 去重测试。
- [x] 覆盖短 flat text、超长单 paragraph、empty 和正常 sectioned 投影。
- [x] 覆盖 PlainTextChunker 尺寸、overlap、offset、page 和 SourceRef。
- [x] 覆盖 ReadingBlock 去重、top-k 补位和同 Section 多窗口聚合。
- [x] 覆盖 contextual indexing 跳过、embedding 保留和 empty 清理。
- [x] 覆盖图谱 skip 幂等、旧图清理和 structured/flat 双向切换。
- [x] 覆盖 RAG API 与 MCP `structure_mode` 输出。

## 8. 文档与验证

- [x] 更新 README 的三种投影模式和降级行为。
- [x] 搜索确认不存在 Section 级检索命中去重。
- [x] 运行 RAG 全量测试。
- [x] 运行 MCP RAG 工具测试。
- [x] 运行 `compileall` 和 `git diff --check`。
- [x] 使用 code-simplifier 复核本次改动范围。
