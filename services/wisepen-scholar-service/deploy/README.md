# Scholar 离线维护

以下命令均在仓库根目录运行。线上服务由 `uv run --no-sync python -m scholar.main` 一次启动，查询编码随主服务启动。离线工具在本目录，独立打开
DuckDB 写连接。

## 本次存量灌库

当前已有发布过的 3,224,542 篇基线。运行迁移会冻结旧索引写入，复制到新索引，使用 GPU FP16 proximity
模型生成向量并持久化，写入新索引；全部验证通过后切换 ES alias 和后续发布目标，旧索引保留。

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/migrate_search_index.py --resume
```

`--resume` 复用当前迁移日志、远端任务和向量断点。首次建立迁移日志时不加此参数。默认日志是服务目录下
`.runtime/hybrid-index-migration.json`，数据库是 `.runtime/scholar.duckdb`。迁移验证使用主服务的查询编码接口，因此先启动
Scholar。GPU interpreter 默认 `D:/Rag/.venv/Scripts/python.exe`，可用 `--gpu-python` 指定；使用本地已下载模型，不修改主环境的
CUDA 依赖。

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/migrate_search_index.py --rollback
```

回滚会恢复旧 alias 和 DuckDB 发布目标，解除旧索引写屏障，保留新索引与已有向量。

## Redis 缓存键升级

查询、发现和论文富化缓存现在使用 `compact-v1` 命名空间。旧键不会再被读取；索引迁移会清理当前 alias 的新旧搜索缓存。需要一次性删除旧缓存时，先确认
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
3. 失效对应论文缓存，将变化文档和向量发布到 ES，发布删除记录。
4. 待发布工作完成后推进 OAI 日期；S2 尚未收录的论文保留待办，下一轮重试。

每日任务不启动 GPU，不下载 S2 release/diff 大包。标题或摘要变化会使旧向量失效；S2 尚未提供新向量时，论文仍可参与文本检索。仅引用数据变化时保留有效向量。S2
平台 API 与在线富化共享 Redis 1 RPS 队列，离线请求不受在线队列准入阈值限制。

重放某日起的变化：

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/run.py sync-arxiv --from 2026-10-20
```

请求或发布失败会使命令非零退出，保留待办和已提交数据；重跑原命令继续。每个 DuckDB 文件同时只运行一个离线写任务。

Linux 每日执行模板为本目录 `scholar-sync.service`、`scholar-sync.timer`。修改仓库路径、uv 路径及 EnvironmentFile 后安装到
systemd，执行 `systemctl enable --now scholar-sync.timer`。定时器每日 03:00 运行一条 `sync-arxiv` 命令。

## 看板与进度

```powershell
uv run --no-sync python services/wisepen-scholar-service/deploy/offline_dashboard.py serve --port 19917
uv run --no-sync python services/wisepen-scholar-service/deploy/offline_dashboard.py run --cwd . --name "每日 OAI + S2" daily-sync -- uv run --no-sync python services/wisepen-scholar-service/deploy/run.py sync-arxiv
```

打开 http://127.0.0.1:19917 查看阶段、速率、剩余量和错误日志。看板只读取进度文件，不连接 DuckDB。已通过包装器运行的迁移可用同一任务
ID `hybrid-index-migration` 记录恢复进度。

## 配置

默认从 Nacos 的 `DEFAULT_GROUP / wisepen-scholar-service-dev.yaml` 读取配置，生产环境使用对应 profile。设置
`NACOS_ENABLED=false` 时从服务 `.env` 与进程环境读取业务配置。

| 配置                                     | 用途                                       |
|------------------------------------------|--------------------------------------------|
| ELASTICSEARCH_URIS / USERNAME / PASSWORD | 索引及发布                                 |
| REDIS_URL                                | 缓存失效和 S2 共享队列                     |
| S2_API_KEY                               | 每日 S2 富化与 v2 向量                     |
| SCHOLAR_INDEX_ALIAS                      | 在线读取 alias，默认 wisepen-scholar-arxiv |
| SCHOLAR_EMBEDDING_MODELS_PATH            | 存量本地模型目录，默认服务 models/         |
| SCHOLAR_EMBEDDING_GPU_PYTHON             | 本次灌库的 GPU Python                      |
| FROM_SOURCE_SECRET                       | 迁移验收调用主服务查询编码接口             |

## 文件职责

- `run.py`：首次导入、每日同步、发布的命令入口。
- `migrate_search_index.py`：现有完整索引迁移、验收、切换和回滚。
- `offline/arxiv_oai.py`、`offline/arxiv_sync.py`：OAI 读取与每日流程。
- `offline/s2_api.py`：每日 S2 batch 请求及 v2 向量解析。
- `offline/arxiv.py`、`offline/s2.py`：存量文件导入。
- `offline/embeddings.py`：存量 GPU 编码、向量持久化和发布投影。
- `offline/publish.py`：版本索引发布与增量写入。
- `offline_dashboard.py`、`offline_dashboard.html`：进度展示和命令包装。
