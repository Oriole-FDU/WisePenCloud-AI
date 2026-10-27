# 部署与离线同步

Scholar 监听 19916，通过 Nacos 注册为 `wisepen-scholar-service`，复用现有内部来源鉴权、日志和观测。HTTP 容器不启动导入或同步任务，也不打开 DuckDB。MCP 工具仍由 `wisepen-mcp-service` 提供。

## 配置和构建

prod 配置放在 `DEFAULT_GROUP / wisepen-scholar-service-prod.yaml`，字段见 `platform-settings.yaml`。ES、Redis、内部来源鉴权、S2 平台密钥与可选 HF token 由 Nacos 提供；OpenAlex 用户密钥通过 Chat 工具配置随当前调用传递。

Jenkins 新增默认关闭的 `DEPLOY_SCHOLAR_SERVICE`，启用后使用现有通用 Dockerfile 构建并部署 `scholar-service`。Compose 中使用 `scholar` profile，显式启动可执行：

```sh
docker compose -f docker-compose-app.yml --profile scholar up -d scholar-service
```

需要遗留中间件网络时，和现有服务一样叠加 `docker-compose-app.legacy-net.yml`。部署环境提供 `DOCKER_REGISTRY`、`APP_VERSION`、`NACOS_USERNAME`、`NACOS_PASSWORD`；不将实际凭证写入镜像或本文件。

`../data/scholar` 挂载到服务的 `.runtime`，供独立 ETL 容器持久保存 DuckDB 和差分下载。HTTP 只读取已发布 ES 别名和 Redis。原始 snapshot 不打进镜像；首次导入应额外挂载原始文件，或先在独立离线环境构建数据库。

## 初始化与每日任务

先完成 arXiv snapshot、指定 release 的 S2 papers/tldrs 导入及 publish；S2 差分首轮必须基于已完整导入并发布的 release。不要同时运行多个写入同一 DuckDB 的 ETL；数据库锁会直接暴露冲突。

```sh
python -m scholar.etl.cli sync-arxiv
python -m scholar.etl.cli sync-s2 --base-release 2026-10-20
```

`sync-arxiv --from YYYY-MM-DD` 可显式指定重放日期；默认使用已确认服务端日期，首次采用计划规定的保守重叠日期。OAI 的续页 token 保持不透明；整轮来源读取、缓存失效与发布完成后才推进日期。429/503 和网络错误直接使本次任务失败，保留可重放进度。

`sync-s2 --target-release RELEASE` 可冻结显式目标；默认读取 latest。首次使用显式 `--base-release`，后续使用持久化完成 release。差分按 Corpus ID 替换或删除事实，只重建受影响的 ES 文档；失效对应 Redis aliases 后推进 release。签名对象下载不携带平台密钥，也不占 S2 API 的发送许可；release/diff API 和在线富化共用 Redis 1 RPS 配额。

`deploy/scholar-sync.service` 与 `.timer` 是待安装的 systemd 模板，每日 03:00 顺序运行两条独立 ETL 命令。设置模板中的仓库绝对路径，并在 `/etc/wisepen/scholar-sync.env` 提供上述部署变量及 `SCHOLAR_S2_BASE_RELEASE`。模板不包含实际凭证，本次开发不会自动安装或启用服务器定时器。

任务输出逐批 JSON 日志，失败由 systemd 标记；重跑原命令恢复。HTTP 健康检查继续使用 `/scholar/health` 和内部来源请求头，确认 ES、Redis 与已发布别名；它不代表某轮 ETL 已完成。

## 当前验证范围

Compose 配置已通过 `docker compose ... config --quiet` 与 YAML 解析；本机 Docker daemon 未运行，镜像构建和服务器部署尚未执行。发布删除与部分失败重放、OAI 游标及 S2 差分通过独立数据库和外部边界替身验证；真实供应商和全量导入状态另见阶段验收记录。
