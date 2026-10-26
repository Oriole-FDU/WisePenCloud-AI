# WisePen Scholar Service

Scholar 是内部 HTTP 微服务。MCP 工具由现有 `wisepen-mcp-service` 注册，Scholar 不启动独立 MCP server。

## 结构与迭代边界

- `api/schemas` 在 HTTP 边界校验完整契约；`api/endpoints` 只做 DTO 转换、预算控制及容器注入。
- `application/search.py`、`lookup.py` 分别拥有检索与精确查询用例；应用层依赖领域仓储端口，不依赖 ES、Redis 驱动。
- `domain/models/paper.py`、`identifiers.py` 保存领域论文及身份；请求、结果、执行状态属于具体 application 用例。Paper 的来源附加事实使用 `metadata[来源名]` 自由字典，约定优于配置，不依赖 ArxivMetadata 类型。
- `providers/field_mapping.py` 统一负责通用领域到各聚合搜索源的映射，目前只维护已使用的 arXiv 映射；`providers` 用于后续学术供应商适配。`domain/interfaces` 暂留空，接入真实供应商时再确定接口。
- `core/storage` 实现 ES 论文读模型和 Redis 查询缓存；`container.py` 装配依赖。
- `core/config` 与 Chat/Sandbox 一样，通过 Bootstrap 定位 Nacos，再由 AppSettings 加载业务字段；`main.py` 负责日志、观测、安全中间件、客户端生命周期和服务注册。
- `etl` 独立维护 DuckDB 来源事实及发布进度；HTTP 不读写 DuckDB，不启动 ETL。

P1 提供本地 arXiv 的 `search_papers` 与 `lookup_papers(purpose=details)`。参数枚举保留计划中完整合同；未接入的多源计划和富化 purpose 明确返回能力不可用。General 只代表本地 arXiv 覆盖，CS 查询此阶段返回基础论文，HF 专用富化在 P5 接入。

后续迭代保留这些入口及职责：P2 扩展离线来源事实和 ES 投影，P3 在应用检索计划中接入 OpenAlex 来源与现有用户配置，P4 为 lookup 增加富化用例，P5 接入 HF 适配器及 discovery，P6 增加独立同步任务。不会用阶段名称建立另一套 API 或总服务。

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
uv run --no-sync scholar-etl import-arxiv
uv run --no-sync scholar-etl status
uv run --no-sync scholar-etl publish
```

默认输入 `third_party_data/arxiv-metadata-oai-snapshot.json`，数据库 `.runtime/scholar.duckdb`。输入中的重复 arXiv ID 按最新 `update_date` 保留，同日取文件中后出现的记录。`--source`、`--db`、`--batch-size` 支持显式覆盖。`publish --alias` 可指定独立验收别名；正式别名默认 `wisepen-scholar-arxiv`。

导入按批次同时提交原始记录、规范投影、待发布 ID 和字节游标；直接重跑从 checkpoint 继续。相同输入重复运行不增加论文；源文件路径、大小或修改时间变化时拒绝复用原数据库，重建应指定新的 `--db`。DuckDB 文件锁保证单写者，不同时运行两个 ETL 写进程。

`--max-batches N` 限制本次工作量，适合验证恢复；未读到 EOF 的导入不能发布。发布逐项确认 Bulk，成功项移出待发布集合，失败项重放同一文档 ID。全部文档数量及内容抽样检查通过后，才原子切换版本索引别名。旧索引不自动删除。在线请求总预算默认 25 秒；独立 ETL 的单次存储请求超时默认 120 秒，通过 `SCHOLAR_ETL_REQUEST_TIMEOUT_SECONDS` 配置。资源有限的 ES 实例可以用 `publish --batch-size 1000` 减少单次写入压力。

## HTTP 与 MCP

`POST /scholar/papers/search` 接受 query、field、published_after/before、limit、source_scope。时间按首次提交日期包含两端过滤；领域采用 plan.md 的 arXiv 分类映射。查询缓存默认 10 分钟，缓存只存论文事实，每次重新构造执行情况。

`POST /scholar/papers/lookup` 接受 identifiers、purpose，逐项保留输入顺序、重复输入、未找到、标识错误或 DOI 歧义。arXiv 版本和官方 URL 归一化，DOI 不区分大小写。没有本地来源映射的领域不会退回全库检索。所有业务响应使用 Common `R<T>`。

现有 MCP 服务的 `search_papers`、`lookup_papers` 使用 Nacos RpcClient 调用这些 HTTP 接口，返回解包后的 data；来源路由和论文归一化不复制到 MCP。

## 验证

```powershell
uv run --no-sync python -m pytest tests -q
```

[数据基线](docs/data-baseline.md)记录输入及样本验证范围；[阶段验收记录](docs/p0-p1-acceptance.md)记录真实基础设施与全量导入结果。单元测试中的外部依赖替身不替代真实 ES 验收。
