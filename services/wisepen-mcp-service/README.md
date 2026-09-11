# WisePen MCP Service

Internal MCP service for AI-safe Wisepen capability tools.
# RAG 能力

MCP 暴露通用 RAG 检索、图谱检索、Page/Section 读取和目录导航能力。工具只暴露查询文本、资源/Section 标识和受范围约束的通用控制参数；候选预算、图谱遍历深度、邻域步数和大纲层级均有服务端默认值与上限，避免模型注入任意底层策略参数。

当前不暴露垂类 `plugin_id`、`metadata_filter`，也不接入 Chat 工具编排。MCP 通过 `RagServiceClient` 调用 `wisepen-rag-service` HTTP 接口，权限身份沿用现有安全 headers。
