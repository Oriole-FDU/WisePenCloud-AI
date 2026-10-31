# 本地离线任务看板

在 Scholar 服务目录运行，不需要 Docker、Nacos 或新的依赖：

```powershell
uv run --no-sync python deploy/offline_dashboard.py serve
```

打开 <http://127.0.0.1:19917>。看板每 3 秒读取 `.runtime/offline-dashboard/` 的任务快照和日志，不连接 DuckDB 或
Elasticsearch，不抢占 ETL 的数据库文件锁。

在另一个终端用包装器运行已有离线命令，进度便会自动接入看板：

```powershell
uv run --no-sync python deploy/offline_dashboard.py run --name "合并已下载的 S2 论文信息" s2-import -- uv run --no-sync python deploy/run.py import-s2 --release-id 2026-10-20
uv run --no-sync python deploy/offline_dashboard.py run --name "论文入库" publish -- uv run --no-sync python deploy/run.py publish --batch-size 1000
```

包装器原样执行命令、保存 stdout/stderr，并以真实退出码记录完成或失败。命令本身所需的环境变量仍由当前终端提供；不要把凭证写入命令参数。同名任务重跑会更新状态并追加日志。
**上述两个写库任务应串行运行**，看板不负责调度任务，也不改变原命令的 checkpoint、事务或恢复语义。

界面展示阶段、最近进度时间、计数、同一阶段的处理速率及日志尾部。`committed: false` 的计数只代表事务内处理量；任务失败时不能算作已入库数量。长
SQL 没有新的进度事件时仍显示进程状态及最后进度时间，不用“日志停更”猜测任务失败。监控包装器异常消失时显示中断及未知退出码，不宣称任务成功。

可以额外查看已存在的历史日志：

```powershell
uv run --no-sync python deploy/offline_dashboard.py serve --log "前次投影=.runtime/project-s2-bounded.log"
```

历史日志只展示原文，不能证明进程仍在运行。可选的 `.runtime/offline-dashboard/baseline.json` 用于记录一次性核对的数据基线，格式为
`{"recorded_at":"ISO-8601 UTC 时间","items":[{"label":"本地论文","value":3224542}],"note":"核对范围说明"}`；它不会随 ETL
自动更新，页面明确保留核对时间。

看板只监听 `127.0.0.1`，不提供网页执行命令、停止任务或生产部署入口。关闭看板不影响包装器及离线任务；重新启动会继续读取保留的快照。

原生混合检索灌库使用 `deploy/migrate_qdrant.py --resume --batch-size 2048 --concurrency 4`（首次运行省略 `--resume`），仍通过 `offline_dashboard.py run` 包装。请求体采用 NumPy float32 与 orjson 直接编码，再使用 gzip level=1 压缩；多个 `wait=false` 请求并发上传，按源批次顺序确认 WAL 接收断点，失败后重放未确认批次。`accepted` 只代表已接收；全部提交后进入 `applying`，等待精确点数追平且后台更新队列清空，再构建索引和执行验收。看板分别显示已接收、已应用及后台待应用批次，并显示批次大小、并发数、组装/提交耗时和压缩前后请求体大小；BM25 三字段及完整论文 payload 比仅上传稠密向量更大。当前批量任务使用 `--batch-size 2048 --concurrency 4`；`--concurrency` 可调整，默认值为 4。灌库验收后停在 `ready_for_cutover`，只有显式 `--cutover` 才切换别名。

稠密向量和 HNSW 的加载时缓存配置、已有集合调整方式及内存成本见 [论文集合的内存缓存](qdrant-memory.md)。存储策略修改后的后台索引转换同样使用上述 `--resume` 监督和验收。

完整检索索引迁移也通过上述 `run` 包装器启动，命令和失败恢复步骤见 [检索初版说明](search-baseline.md#执行完整迁移)
。看板展示冻结、回填、验收、切换、完成或恢复阶段，以及新旧索引名和已复制数量；成功退出后才表示迁移完成。

2026-10-31 开发环境吞吐观测：原 256 篇/批、2 路 SDK 写入约 325 篇/秒；改为 NumPy/orjson 直接 HTTP 编码、gzip 和 2048 篇/批、4 路并发后，约 71 秒窗口达到 745 篇/秒。1024 篇/批、8 路并发未继续提升，出现更长服务端等待。上述速率包含完整 payload 与三路服务器端 BM25 生成，不等同于此前仅写入稠密向量的吞吐。
