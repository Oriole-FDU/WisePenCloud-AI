# WisePen MCP Service

Internal MCP service for AI-safe Wisepen capability tools.

# 平台默认 Web Search

`default_web_search` 使用平台托管的智谱 GLM Web Search，保留原工具名和注册方式。MCP 输入包含必填 `query`、可选 `max_results`（默认 10，范围 1–20）和 `recency`，不暴露 `mode`、`focus` 或用户 API key。

在 MCP 服务的 Nacos 应用配置（`wisepen-mcp-service-dev.yaml` / `wisepen-mcp-service-prod.yaml`）中配置 `WEB_SEARCH_API_KEY`。默认空值会在请求前返回配置缺失错误；请求侧 key 不会覆盖平台密钥。`WEB_SEARCH_GLM_BASE_URL` 默认为 `https://open.bigmodel.cn/api/paas/v4`。

GLM 请求固定使用 `search_std`、`content_size="medium"`、`search_intent=False`。Adapter 将查询截取至 70 字符，公共结果的 `query` 仍保留规范化后的完整查询。每条网页的 `content` 映射为摘要证据 `evidences`，不执行全文抓取，也不生成顶层 `summary`。

# Web Search 新鲜度

`recency` 仅接受 `day/week/month/year`，省略或 `null` 表示不限。它是软新鲜度意图，具体日期边界由 Provider 映射；精确日期要求由调用方写入 `query`，基座不解析查询、不做日期后过滤。

GLM、Exa、Tavily、Firecrawl、百度千帆暴露完整四档。TinyFish、AnySearch 因当前搜索路径无法统一实现四档而不暴露。是否暴露由方法签名推导；拥有独立 academic 路径的工具必须两个方法均声明 `recency`。

Exa 以 UTC 当前时刻回溯 1/7/30/365 天，过滤发表时间；Firecrawl 网页使用原生 `qdr:d/w/m/y`，学术路径按 UTC 日期包含首尾共 1/7/30/365 个自然日。百度 `day` 使用北京时间当天的显式日期上下限：2026-09-15 实测 `now+1d/d` 被拒绝，同一 `YYYY-MM-DD` 的 `gte/lte` 则返回当日多个非零点结果，历史单日查询亦通过。
