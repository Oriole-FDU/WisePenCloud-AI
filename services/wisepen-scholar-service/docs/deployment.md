# Scholar 运行

在仓库根目录执行 `uv run --no-sync python -m scholar.main`。服务监听 19916，注册到 Nacos 的 `wisepen-scholar-service`，加载
CPU 查询编码模型并同时提供论文 API 与 `/v1/embeddings`。HTTP 进程不打开 DuckDB，也不启动离线导入。

业务配置来自 `DEFAULT_GROUP / wisepen-scholar-service-{PROFILE}.yaml`。`NACOS_ENABLED=false` 时关闭配置拉取和注册，从服务
`.env` 与进程环境读取相同配置。

平台凭证配置为 `S2_API_KEY`、`OPENALEX_API_KEY`、`HF_TOKEN`。OpenAlex 与 HF 支持请求 BYOK 优先；S2 使用平台密钥和 Redis
共享队列。模型位置由 `SCHOLAR_EMBEDDING_MODELS_PATH` 指定。

HTTP 请求使用内部来源请求头；`/scholar/health` 检查 ES、Redis、alias，模型启动完成后 `/health` 返回查询编码就绪。MCP 入口由
wisepen-mcp-service 提供。

存量灌库、每日 OAI + S2 API、systemd 模板和恢复命令见 [离线维护](../deploy/README.md)。每日流程不使用 S2 diff，不运行 GPU。

已有 Jenkins/Compose 的 Scholar profile 可用于目标环境部署。修改配置、挂载持久化 `.runtime` 与模型目录后按仓库既有发布流程启动；本机开发使用上述
uv 命令。
