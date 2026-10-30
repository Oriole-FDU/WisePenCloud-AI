# Scholar 运行

在仓库根目录执行 `uv run --no-sync python -m scholar.main`。服务监听 19916，注册到 Nacos 的 `wisepen-scholar-service`，加载
CPU 查询编码模型并同时提供论文 API 与 `/v1/embeddings`。HTTP 进程不打开 DuckDB，也不启动离线导入。

业务配置来自 `DEFAULT_GROUP / wisepen-scholar-service-{PROFILE}.yaml`。`NACOS_ENABLED=false` 时关闭配置拉取和注册，从服务
`.env` 与进程环境读取相同配置。

上线前先将 [配置模板](platform-settings.yaml) 中完整的 `SCHOLAR_POLICIES` 加入 Nacos，再部署新代码；首次缺失策略或策略无效会启动失败。领域策略和通用富化顺序支持热更新，其他配置仍需重启。多实例各自在收到通知后切换；恢复旧策略时重新发布上一份有效 YAML 即可，不需要清理 Redis。

`NACOS_ENABLED=false` 时，`SCHOLAR_POLICIES` 环境变量使用 YAML 或 JSON 文本，例如 `.env` 中使用单引号包裹完整 JSON 对象。此模式不会启动监听。

平台凭证为 `S2_API_KEY`、`OPENALEX_API_KEY`、`HF_TOKEN`，仅用于 lookup 和富化，用户 BYOK 优先。HF/OpenAlex 搜索及 HF 发现必须使用用户 BYOK，缺 key 时不读取该来源搜索缓存，也不回退平台 key。S2 用户 key 使用独立 Redis FIFO；在线与离线各自使用 NOKEY → 429 → KEY 双通道及独立合流队列，同一平台 key 的发射锚点共享且在线兜底优先。模型位置由 `SCHOLAR_EMBEDDING_MODELS_PATH` 指定。

PDF 补救使用 `UNPAYWALL_EMAIL`，默认 `jzsun24@m.fudan.edu.cn`；单次请求超时 5 秒、并发 4，结果使用独立 DOI 缓存。

HTTP 请求使用内部来源请求头；`/scholar/health` 检查 ES、Redis、alias，模型启动完成后 `/health` 返回查询编码就绪。MCP 入口由
wisepen-mcp-service 提供。

存量灌库、每日 OAI + S2 API、systemd 模板和恢复命令见 [离线维护](../deploy/README.md)。每日流程不使用 S2 diff，不运行 GPU。

已有 Jenkins/Compose 的 Scholar profile 可用于目标环境部署。修改配置、挂载持久化 `.runtime` 与模型目录后按仓库既有发布流程启动；本机开发使用上述
uv 命令。
