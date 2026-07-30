# WisePen RAG Service

独立负责私有知识库内容投影、ACL 投影、混合检索、知识图谱和知识导航。

服务通过 Nacos 注册为 `wisepen-rag-service`。文档、ACL 和删除事件由 Kafka
消费；在线知识导航通过 `/internal/rag/knowledge-navigation/*` 提供给 MCP 服务。

RAG 的 agent tool 定义不在本服务中，统一由 `wisepen-mcp-service` 暴露。

本地从 `src/rag` 启动可使用 `uv run python main.py`；从 `src` 启动可使用
`uv run python -m rag.main`。
