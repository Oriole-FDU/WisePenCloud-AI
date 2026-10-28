# WisePen Scholar Service

Scholar 是内部 HTTP 微服务。MCP 工具由现有 `wisepen-mcp-service` 注册，Scholar 不启动独立 MCP server。

## 结构与迭代边界

- `api/schemas` 在 HTTP 边界校验完整契约；`api/endpoints` 只做 DTO 转换、预算控制及容器注入。
- application 提供 search、lookup、discover 三个用例（`search.py`、`lookup.py`、`discovery.py`）。`enrichment.py` 是检索后处理与 lookup 共用的内部执行机制；`field_policies.py` 声明有序 searchers、领域增量 enrichers 和 discoverer。
- `domain/models/paper.py`、`identifiers.py` 保存领域论文及身份；请求、结果、执行状态属于具体应用用例或共享执行模型。`Paper.source` 接受开放来源字符串，来源附加事实放在 `metadata[来源名]` 自由字典中，不依赖供应商专用领域类型。
- `domain/interfaces` 按搜索、富化、发现能力声明通用端口；provider 可实现一个或多个能力。`providers/field_mapping.py` 仍是领域分类到 arXiv/OpenAlex 来源参数的统一映射文件。
- `core/storage` 实现 ES 论文读模型和 Redis 查询缓存；`container.py` 装配依赖。
- `core/config` 与 Chat/Sandbox 一样，通过 Bootstrap 定位 Nacos，再由 AppSettings 加载业务字段；`main.py` 负责日志、观测、安全中间件、客户端生命周期和服务注册。
- `etl` 独立维护 DuckDB 来源事实及发布进度；HTTP 不读写 DuckDB，不启动 ETL。

P1 本地 arXiv、P3 配置驱动检索、P4 lookup 在线富化已验收；P2 正在合并本地 S2 信息并更新检索索引。P5 发现按 HF 官方 SDK 的请求与返回流程实现。CS 搜索与发现通过原生异步 HTTP 调用 HF。

P6 已提供独立 OAI/S2 差分同步与恢复、Compose/Jenkins/systemd 模板；真实完整同步的剩余验收见 [P6 记录](docs/p6-acceptance.md)。部署交付仅包含配置和说明，本次不启动 Docker、不构建镜像、不做生产部署。

单源检索按优先级回退，仅外部来源失败尝试下一项，正常空列表也结束。多源全部执行并报告实际成功和失败；全部失败抛原始异常。程序错误和取消直接传播。

通用富化依次 arxiv→s2→openalex，每篇有有效信号即停止；领域增量全部执行，初始仅 CS 注册 HF。搜索成功来源包含任一通用来源时跳过通用富化，领域富化排除本次成功的同源 searcher。供应商规则位于适配器及注册绑定，新增临床增量只需 provider、注册和领域配置。平台不默认融合同名字段或做语义去重。

## 配置与启动

在本目录复制 `.env.example` 为 `.env`，填写现有 Nacos 引导凭证。业务配置使用 DEFAULT_GROUP 下的 `wisepen-scholar-service-dev.yaml`，字段见 [配置模板](docs/platform-settings.yaml)。prod 对应 `wisepen-scholar-service-prod.yaml`。不把凭证提交进仓库。

在本目录执行：

```powershell
uv sync --inexact --package wisepen-scholar-service --extra test
uv run --no-sync uvicorn scholar.main:app --host 127.0.0.1 --port 19916
```

`GET /scholar/health` 检查真实 ES、Redis 及已发布别名，未就绪返回 503。与业务 API 一样，需要现有内部 `X-From-Source` 请求头。

## 初始化和恢复

```powershell
uv run --no-sync python deploy/run.py import-arxiv
uv run --no-sync python deploy/run.py status
uv run --no-sync python deploy/run.py publish
```

默认输入 `third_party_data/arxiv-metadata-oai-snapshot.json`，数据库 `.runtime/scholar.duckdb`。输入中的重复 arXiv ID 按最新 `update_date` 保留，同日取文件中后出现的记录。`--source`、`--db`、`--batch-size` 支持显式覆盖。`publish --alias` 可指定独立验收别名；正式别名默认 `wisepen-scholar-arxiv`。

导入按批次同时提交原始记录、规范投影、待发布 ID 和字节游标；直接重跑从 checkpoint 继续。相同输入重复运行不增加论文；源文件路径、大小或修改时间变化时拒绝复用原数据库，重建应指定新的 `--db`。DuckDB 文件锁保证单写者，不同时运行两个 ETL 写进程。

`--max-batches N` 限制本次工作量，适合验证恢复；未读到 EOF 的导入不能发布。发布逐项确认 Bulk，成功项移出待发布集合，失败项重放同一文档 ID。全部文档数量及内容抽样检查通过后，才原子切换版本索引别名。旧索引不自动删除。在线请求总预算默认 25 秒；独立 ETL 的单次存储请求超时默认 120 秒，通过 `SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS` 配置。资源有限的 ES 实例可以用 `publish --batch-size 1000` 减少单次写入压力。

离线任务可用[本地监控看板](docs/offline-dashboard.md)查看阶段进度、速率、真实退出码和错误日志。看板仅读取快照，不轮询数据库；现有 ETL 命令经 `deploy/offline_dashboard.py run` 包装后自动接入。

## HTTP 与 MCP

`POST /scholar/papers/discover` 与现有 MCP `discover_papers` 提供 CS 的 daily/weekly/monthly 渠道发现，默认 trending，支持 recent 和 limit，不接受 query。每次只发一个官方列表请求，直接交付成功响应；daily 使用官方最新内容默认值，weekly/monthly 传周期参数。不按渠道日期丢弃或过滤结果，见 [P5 验收记录](docs/p5-acceptance.md)。

Lookup 的 `details/enrich/full` 已接入同一个用例。details 可复用缓存且不在线调用 enricher；enrich 只输出精选信号；full 同时返回可用基础论文。没有可用结果的外部失败直接抛出，已有论文或信号的部分结果保留真实错误。缓存与真实边界验收见 [P4 记录](docs/p4-acceptance.md)。

`POST /scholar/papers/search` 接受 query、field、published_after/before、limit、source_scope。时间按首次提交日期包含两端过滤；领域采用 plan.md 的 arXiv 分类映射。查询缓存默认 10 分钟，缓存只存论文事实，每次重新构造执行情况。

本地检索初版采用 English 子字段上的 BM25F 近似召回，标题/TLDR/摘要权重为 3:2:1，原词形和短语匹配加分，引用数不参与排序。新查询需要先完整回填子字段后再启用；算法、参数和不干扰现有离线任务的迁移边界见[检索基线](docs/search-baseline.md)。

`POST /scholar/papers/lookup` 接受 identifiers、purpose、field（默认 general），逐项保留输入顺序、重复输入、未找到、标识错误或 DOI 歧义。arXiv 版本和官方 URL 归一化，DOI 不区分大小写。没有本地来源映射的领域不会退回全库检索。所有业务响应使用 Common `R<T>`。

现有 MCP 服务的 search、lookup、discover 工具使用 Nacos RpcClient 调用这些 HTTP 接口，返回解包后的 data；来源路由和论文归一化不复制到 MCP。配置驱动的检索及医疗多源扩展验收见 [P3 记录](docs/p3-acceptance.md)。

S2 在线请求使用 Redis FIFO 与离线步骤共享 1 RPS。Nacos `SCHOLAR_S2_MAX_QUEUED_AHEAD` 默认 5，前方超过 5 项立即跳过并回退 OpenAlex；离线不应用拥堵阈值。`execution.queued_ahead` 展示准入快照，缓存命中不携带旧排队信息。

## 验证

```powershell
uv run --no-sync python -m pytest tests -q
```

[数据基线](docs/data-baseline.md)记录输入及样本验证范围；[阶段验收记录](docs/p0-p1-acceptance.md)记录真实基础设施与全量导入结果。单元测试中的外部依赖替身不替代真实 ES 验收。

真实 Redis 队列验收使用独立随机命名空间，显式设置 `SCHOLAR_TEST_REDIS_URL` 后执行 `uv run --no-sync pytest tests/test_s2_queue.py -q`。未配置时这 5 项跳过，不连接默认服务命名空间。
