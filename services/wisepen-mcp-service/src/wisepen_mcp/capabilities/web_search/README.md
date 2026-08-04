# Web 搜索能力 (Web Search capability)

该能力包含了 MCP 服务暴露的完整 Web 搜索工具边界：包含 7 个 FastMCP 工具、提供商协议适配器（Provider protocol adapters）、排序流水线（Ranking pipeline）及其返回模型。

```text
FastMCP tool
    -> WebSearchService
    -> SearchSourceFactory
    -> provider searcher
    -> SearchPipeline
    -> wisepen_mcp.utils.ranking
    -> WebSearchToolResult

```

提供商的 API 密钥（API keys）通过 MCP 请求元数据（Metadata）传入。它们决不能声明为模型可见的工具参数，也绝不可存储在应用设置或日志中。

## 新增搜索提供商 (Adding a provider)

1. 通过一个最小化 API 调用，验证实际的请求方法、路径、鉴权请求头（Authentication header）、Payload 以及响应字段。
2. 将其固定的名称添加到 `SearchProviderName` 中。
3. 仅在 `services/providers/` 目录下实现外部协议适配器。将结果转换为 `SearchResponse`，且仅保留排序流水线或面向 Agent 的返回模型所消耗的字段。
4. 在 `SearchSourceFactory` 中添加该提供商的构造逻辑和基础 URL（Base URL）。
5. 在 `tools.py` 中注册一个 FastMCP 工具，并在 `SystemMcpToolCatalog` 中添加其 Chat 侧的策略/配置叠加层（Policy/config overlay）。
6. 添加 `httpx.MockTransport` 测试覆盖，包含请求鉴权、Payload 以及响应映射。

`search_academic()` 可以回退（Fall back）到普通 Web 搜索。仅当提供商拥有真实的学术端点（Academic endpoint）或已文档化的研究筛选器（Research filter）时，才可重写该方法。搜索提供商绝不能默许地（Silently）添加爬虫、抓取、浏览器或 Agent 行为。
