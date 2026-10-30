# Scholar 离线维护

以下命令均在仓库根目录运行。线上服务由 `uv run --no-sync python -m scholar.main` 一次启动，查询编码随主服务启动。离线工具在本目录，独立打开
DuckDB 写连接。

## 已有向量迁移到 Qdrant

迁移复用 DuckDB 中已有的 768 维论文向量，不重新运行模型。脚本按 `specter.py` 的模型签名和标题/摘要输入指纹检查向量，使用稳定 arXiv UUID 分批写入
Qdrant；完成后核对集合计数并恢复 HNSW 建图。ES alias 不参与这次迁移，也不需要启动 Scholar 或 GPU。

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/migrate_qdrant.py --resume
```

`--resume` 使用 `.runtime/qdrant-migration.json` 中记录的模型签名和已确认游标继续上传，数据库默认是 `.runtime/scholar.duckdb`。目标集合名由模型签名派生；
当前集合必须为空，续跑时使用同一迁移日志。迁移只移动已有向量，不改变 arXiv 事实或 ES alias。

在线查询由 Qdrant 按学科与含边界的 UTC 提交日期区间预过滤向量，并只返回排序后的 arXiv ID；论文事实仍从 ES 批量读取。每日发布先写 ES 全文文档，再写入或删除
Qdrant 向量，两边操作成功后才确认对应 DuckDB 待办。ES 与 Qdrant 之间没有跨库事务；一端失败时待办保留，重跑会使用相同论文身份继续发布。

## Redis 缓存键升级

查询、发现和论文富化缓存现在使用 `compact-v1` 命名空间。旧键不会再被读取；Qdrant 向量迁移不切换 ES alias，也不清理 Redis 缓存。需要一次性删除旧缓存时，先确认
`index_alias` 与当前 `SCHOLAR_INDEX_ALIAS` 一致、`REDIS_URL` 指向服务使用的 Redis 数据库，再执行以下命令。富化模式会覆盖该索引下所有来源及
BYOK 凭证 scope；不执行 `FLUSHDB`。

```bash
index_alias="${SCHOLAR_INDEX_ALIAS:-wisepen-scholar-arxiv}"
for pattern in \
  "scholar:search:${index_alias}:*" \
  "scholar:discovery:${index_alias}:*" \
  "scholar:enrichment:${index_alias}:*"
do
  redis-cli -u "$REDIS_URL" --scan --pattern "$pattern" |
    xargs -r -n 200 redis-cli -u "$REDIS_URL" UNLINK
done
```

新环境从原始文件初始化时依次运行：

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py import-arxiv
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py import-s2 --release-id 2026-10-20 --papers-dir s2_download/papers --tldrs-dir s2_download/tldrs
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py encode-embeddings
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py publish
```

`import-s2` 只读取首次灌库已经下载的 papers/tldrs 文件。release-id 填该批文件的实际版本。`encode-embeddings`
只用于存量灌库；已完成的向量按输入指纹复用。发布成功后开始每日任务。

## 每日 OAI + S2 API 同步

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py sync-arxiv
```

这条命令依次执行：

1. 从上次完成日期读取 arXiv OAI 变化，保存新增、修改和删除记录。
2. 将变化论文和待补充论文分批请求 S2 Graph API，显式指定 `embedding.specter_v2`，保存引用指标、TLDR 和 768 维 v2 向量。
3. 失效对应论文缓存，将全文文档发布到 ES，将仍匹配模型及输入指纹的向量发布到 Qdrant，并发布删除记录。
4. 待发布工作完成后推进 OAI 日期；S2 尚未收录的论文保留待办，下一轮重试。

每日任务不启动 GPU，不下载 S2 release/diff 大包。标题或摘要变化会使旧向量失效；S2 尚未提供新向量时，论文仍可参与文本检索。仅引用数据变化时保留有效向量。S2
在线与离线使用独立的 Redis 合流双通道：先 NOKEY，仅 429 投递 KEY 兜底；每批最多 `SCHOLAR_S2_BATCH_MAX_IDS`（默认 300）。两路默认各间隔 1.5 秒，分别由 `SCHOLAR_S2_NOKEY_INTERVAL_SECONDS`、`SCHOLAR_S2_KEY_INTERVAL_SECONDS` 配置。离线不受在线队列准入阈值限制，与在线 KEY 共用平台凭证的发射锚点，在线兜底优先；CLI 负责完整消费者生命周期。

重放某日起的变化：

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py sync-arxiv --from 2026-10-20
```

S2 每批最多重试 5 次，耗尽后保留日常待办并继续；已确认未收录或缺核心信号/向量的论文转入 `pending_s2_core_missing`，由 `recover-s2` 单独恢复。发布失败会使命令非零退出，保留待办和已提交数据；重跑原命令继续。每个 DuckDB 文件同时只运行一个离线写任务。

Linux 每日执行模板为本目录 `scholar-sync.service`、`scholar-sync.timer`。修改仓库路径、uv 路径及 EnvironmentFile 后安装到
systemd，执行 `systemctl enable --now scholar-sync.timer`。定时器每日 03:00 运行一条 `sync-arxiv` 命令。

## 看板与进度

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/offline_dashboard.py serve --port 19917
uv run --no-sync python services/wisepen-scholar-service/deploy/offline_dashboard.py run --cwd . --name "每日 OAI + S2" daily-sync -- uv run --no-sync python services/wisepen-scholar-service/deploy/run.py sync-arxiv
```

打开 http://127.0.0.1:19917 查看阶段、速率、剩余量和错误日志。看板只读取进度文件，不连接 DuckDB。迁移日志记录 Qdrant 已确认的上传游标，可在原命令后追加
`--resume` 恢复。

## 配置

默认从 Nacos 的 `DEFAULT_GROUP / wisepen-scholar-service-dev.yaml` 读取配置，生产环境使用对应 profile。设置
`NACOS_ENABLED=false` 时从服务 `.env` 与进程环境读取业务配置。

| 配置                                     | 用途                                       |
|------------------------------------------|--------------------------------------------|
| ELASTICSEARCH_URIS / USERNAME / PASSWORD | 索引及发布                                 |
| QDRANT_URL / QDRANT_API_KEY              | 向量集合迁移、在线召回及发布               |
| REDIS_URL                                | 缓存失效和 S2 共享队列                     |
| S2_API_KEY                               | 每日 S2 富化与 v2 向量                     |
| SCHOLAR_INDEX_ALIAS                      | 在线读取 alias，默认 wisepen-scholar-arxiv |
| SCHOLAR_EMBEDDING_MODELS_PATH            | 存量本地模型目录，默认服务 models/         |
| SCHOLAR_EMBEDDING_GPU_PYTHON             | `encode-embeddings` 存量编码的 GPU Python  |
| FROM_SOURCE_SECRET                       | 迁移验收调用主服务查询编码接口             |

## 文件职责

- `run.py`：首次导入、每日同步、发布的命令入口。
- `migrate_qdrant.py`：将 DuckDB 中已验证的向量分批迁移到 Qdrant 并支持续传。
- `offline/arxiv_oai.py`、`offline/arxiv_sync.py`：OAI 读取与每日流程。
- `offline/s2_api.py`：每日 S2 batch 请求及 v2 向量解析。
- `offline/arxiv.py`、`offline/s2.py`：存量文件导入。
- `offline/embeddings.py`：存量 GPU 编码、向量持久化和输入指纹校验。
- `offline/publish.py`：版本索引发布与增量写入。
- `offline_dashboard.py`、`offline_dashboard.html`：进度展示和命令包装。
