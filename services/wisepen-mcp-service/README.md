# WisePen MCP Service

Internal MCP service for AI-safe Wisepen capability tools.

# 平台默认 Web Search

`default_web_search` 使用平台托管的智谱 GLM Web Search，保留原工具名和注册方式。MCP 输入仅包含必填 `query` 和可选 `max_results`（默认 10，范围 1–20），不暴露 `mode`、`focus` 或用户 API key。

在 MCP 服务的 Nacos 应用配置（`wisepen-mcp-service-dev.yaml` / `wisepen-mcp-service-prod.yaml`）中配置 `WEB_SEARCH_API_KEY`。默认空值会在请求前返回配置缺失错误；请求侧 key 不会覆盖平台密钥。`WEB_SEARCH_GLM_BASE_URL` 默认为 `https://open.bigmodel.cn/api/paas/v4`。

GLM 请求固定使用 `search_std`、`content_size="medium"`、`search_intent=False`。Adapter 将查询截取至 70 字符，公共结果的 `query` 仍保留规范化后的完整查询。每条网页的 `content` 映射为摘要证据 `evidences`，不执行全文抓取，也不生成顶层 `summary`。

# RAG 能力

MCP 暴露通用 RAG 检索、图谱检索、Page/Section 读取和目录导航能力。工具只暴露查询文本、资源/Section 标识和受范围约束的通用控制参数；候选预算、图谱遍历深度、邻域步数和大纲层级均有服务端默认值与上限，避免模型注入任意底层策略参数。

当前不暴露垂类 `plugin_id`、`metadata_filter`，也不接入 Chat 工具编排。MCP 通过 `RagServiceClient` 调用 `wisepen-rag-service` HTTP 接口，权限身份沿用现有安全 headers。
