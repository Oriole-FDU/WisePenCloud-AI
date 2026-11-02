> 当前检索配置见 [混合检索](hybrid-search.md)，离线操作见 [维护文档](../deploy/README.md)。下文保留旧文本基线记录。

# 本地论文检索初版

本版使用 Elasticsearch 原生 BM25/BM25F 文本排序，不新增模型调用、引用数重排或评测平台。参数是可解释的初始先验，不宣称是论文检索的通用最优值。

## 召回与排序

1. `title`、`abstract`、`metadata.s2.tldr.text` 增加使用 `english` analyzer 的 `.en` 子字段；原字段继续使用 `standard`。
   `.en` 负责英语词形归并和停用词处理，原字段用于未词干词形加分。原字段仍是分词文本，不是整串或区分大小写的精确匹配。
2. 使用 `combined_fields` 查询三个 `.en` 字段，标题、TLDR、摘要权重为 **3:2:1**。这是 ES 对 BM25F
   的近似实现，可以让查询词分散在标题和摘要中共同满足条件，避免 `most_fields` 把命中门槛分别施加到每个字段。
3. `minimum_should_match: "2<70%"` 在英语分析后应用：一至两个有效词需要全部命中，更多有效词至少命中向下取整的
   70%。停用词不计入该门槛；全是停用词时返回空结果。
4. 原字段用 `most_fields` 作为可选加分，标题/TLDR/摘要分别为 **1/0.5/0.3**。标题 `.en` 连续短语额外 boost **2**；摘要 `.en`
   短语 boost **0.5**、slop **2**。这些子句只给已召回论文加分，不放宽召回条件。
5. 保留 ES 默认 BM25：`k1=1.2`、`b=0.75`。不添加自定义 similarity、脚本评分、模糊匹配或向量字段。

字段 boost 会影响词频、长度归一化和各子句分数，不能把两个 boost 的和解释为固定的最终权重。本版权重表达“标题优先、TLDR
次之、摘要补充”的偏好。

`categories` 和首次提交日期继续作为 filter；作者不参与概念检索。当前没有明确的作者查询输入协议，因此不凭人名启发式改变
query 的含义。专门的作者查询留待有明确需求时独立定义。

引用数和高影响力引用数保留为返回事实，本版不参与排序。零引用、新论文和缺失引用指标不会因此降权。之后若确实需要引用信号，再用有标签的结果验证轻量加分，避免直接相乘抹掉零引用论文的分数。

## 索引启用边界

代码中的 mapping 用于新建索引；给旧索引增加 `.en` 不会自动回填已索引文档。新查询须在 `.en`
回填完整后随服务版本一起启用。迁移由独立离线脚本执行，实际完成状态以迁移 journal 和看板退出码为准。

后续迁移使用一次完整的新索引重建，保留旧索引以便回滚。必须在现有离线发布完成、待办清空并停止写入后执行。除了原子切换 ES
alias，还要协调更新 DuckDB `import_state.target_index`：现有 ETL 将它作为写入目标，单独切 alias 会让后续增量继续写旧索引。迁移期间保持
ETL 停写，失败时恢复 alias 和写入目标，再恢复任务。

重建新索引时可暂设 `refresh_interval=-1`、`number_of_replicas=0`
，完成后恢复预定的刷新及副本策略，再检查数据和切换。现有发布器已关闭新索引批量写入期间的刷新；副本配置与整库迁移不在本次检索算法改动中展开。

### 执行完整迁移

在服务目录执行；配置仍从既有 `.env` / Nacos 加载，不在参数中传凭证：

```powershell
uv run --no-sync python deploy/offline_dashboard.py run --name "检索索引迁移：回填英语词干字段" search-index-migration -- uv run --no-sync python -u deploy/migrate_search_index.py
```

脚本持有 DuckDB 写连接，阻止其他进程运行 ETL；确认导入已发布、待办为零且 alias 与 `target_index`
一致后，给旧索引加写屏障并刷新，保留读服务。新建相同主分片数、临时关闭刷新和副本的索引，通过 ES 异步 `_reindex`
完整重建所有文档，不重扫原始文件、不重写 DuckDB 论文数据。脚本每 15 秒读取这一个任务的进度供看板展示，不轮询全库。

复制完成后恢复旧索引的刷新和副本策略，核对完整复制结果、文档总数、S2/TLDR 覆盖数、样本文档及真正的 BM25F 检索。随后原子切
alias、更新 DuckDB 写入目标、仅失效本服务 alias 下的查询缓存，再确认两端指向相同新索引。旧索引保留且只读；脚本不启动业务服务或部署容器。

复制或验收期间本机断网不意味着 ES 后台任务失败。脚本保留旧索引写屏障、任务 ID 和新索引，记录 `migration_paused`
并报出原网络异常。网络恢复后，以下命令复用原任务继续等待或直接进入验收，不重建索引、不重新复制全库：

```powershell
uv run --no-sync python deploy/offline_dashboard.py run --name "续接检索索引迁移：验收与切换" search-index-migration -- uv run --no-sync python -u deploy/migrate_search_index.py --resume
```

续接只适用于仍指向旧索引、旧索引持续只读且没有新待办的窗口。若原 ES 复制任务本身失败，续接会保留真实错误并进入恢复，不把不完整索引用于检索。

跨 ES / DuckDB 的切换不具备分布式事务：数据校验或切换失败自动恢复两端并重新抛出错误。若切换过程中被强杀或恢复失败，保持 ETL
停止，使用 `.runtime/search-index-migration.json` 恢复记录执行：

```powershell
uv run --no-sync python deploy/offline_dashboard.py run --name "恢复原检索索引与离线写入目标" search-index-rollback -- uv run --no-sync python -u deploy/migrate_search_index.py --rollback
```

回滚只适用于迁移后尚未恢复写入的窗口；恢复 ETL 后，旧索引不再包含新写入，不能直接回滚。成功迁移后可启动包含新版查询的服务。不要在迁移完成前重启到新版查询，也不要在恢复记录存在时直接覆盖它重跑。

## 依据与验证范围

- [Elastic combined_fields](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-combined-fields-query)
  ：BM25F 近似、跨字段按词匹配、相同 analyzer 与字段 boost 至少为 1 的约束。
- [Elastic multi_match](https://www.elastic.co/docs/reference/query-languages/query-dsl/query-dsl-multi-match-query)：
  `most_fields` 的字段级门槛和多字段加分语义。
- [Elastic 多 analyzer 子字段](https://www.elastic.co/docs/reference/elasticsearch/mapping-reference/multi-fields)
  ：词干召回与原词形加分，以及旧文档需要回填的限制。
- [Elastic similarity](https://www.elastic.co/docs/reference/elasticsearch/index-settings/similarity)：默认 BM25 及
  `k1/b` 参数。

本轮 22 项 HTTP/ETL 回归测试通过，并用现有 ES 的独立 11 篇论文小索引验证了词形归并、原词形加分、跨字段召回、停用词、短语排序、TLDR
单独召回、作者隔离和类别/日期过滤。相同文本、引用数分别为 0 和 10,000 的两篇论文得分均为
14.348645，确认引用数不参与排序。临时索引已删除，证据保存在被忽略的 `.runtime/search-v1-check.json`。

不建立 nDCG 标签集或调参流程。以上行为验证不能代表全库相关性评测或已完成索引迁移；服务启用新版查询前还应失效旧的 Scholar
查询结果缓存。
