# TODO

## 评估 `sandbox_read_as_markdown`

文档读取方案 1 暂缓，主线见 [PDF 与 Office 文档读取实施计划](DOCUMENT_READING_PLAN.md)。当方案 2 在真实长文档问答中频繁需要多次搜索、续读，或全文总结的章节覆盖不稳定时，拿同一批样例对比两条路径的工具调用次数、上下文开销、延迟、跨文档定位和章节覆盖，再决定是否落地。

候选做法：新增 `sandbox_read_as_markdown` MCP 工具，在离线 Linux AIO 容器内调用镜像预装的 AnyDoc CLI。转换结果先写工作区文件，再按 `content` 字段返给 Chat，避免命令 `stdout` 截断长文。为该字段声明 `LONG_TEXT`，长文超出预览预算时由 Chat 的现有 `MarkdownChunker` 生成章节和自然块，复用目录、章节续读和 BM25 检索。现有 `read_document_resource_text` 提供 MCP 元数据配置先例。完整 Markdown 经过工具返回链路，评估时同时验证大文件传输和缓存预算。
