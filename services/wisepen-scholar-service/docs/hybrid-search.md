# 混合检索

在仓库根目录运行：

```powershell
uv run --no-sync python -m scholar.main
```

主服务加载 CPU FP32 adhoc query adapter，预热后在 19916 提供 `/scholar/*` 和 OpenAI 兼容 `/v1/embeddings`。两者使用同一内部来源鉴权。
`NACOS_ENABLED=false` 可关闭配置拉取和服务注册，改从 `.env` 与进程环境读取配置。

启动时执行一次 `machine learning`、计算机科学领域的 arXiv 混合检索，使用当前召回参数，覆盖 ES 关键词检索、查询编码、Qdrant 召回与 ES 回填。预热直接调用 arXiv provider，不读取查询缓存或执行在线富化；启动阶段通过进程内 ASGI 调用同一编码路由，完成启动后正常请求继续使用本机 HTTP。启动预热总预算为 `SCHOLAR_REQUEST_TIMEOUT_SECONDS * 3`，默认 **75 秒**；正常在线请求仍使用原配置的 **25 秒**预算。超时、依赖失败或编码降级均使启动失败，空结果允许通过。预热成功后才注册 Nacos 并开放服务，日志记录实际预热预算、耗时、返回数量及失败阶段。

2026-10-31 开发环境冷启动观测：Elasticsearch 首次检索约 **24 秒**，Qdrant 首次向量检索也约 **24 秒**，两段在当前检索链中串行执行。沿用 25 秒总预算时，第一次请求预热完 Elasticsearch 后超时，第二次请求在 Elasticsearch 已热的情况下预热 Qdrant 后仍超时，第三次请求因两者均已预热而迅速完成。这与端到端测试中前两次请求失败、随后恢复的现象一致。因此启动预热采用三倍请求预算，容纳两段串行冷启动及编码、论文回填开销。

工作区使用 `transformers==4.57.6`、`adapters==1.3.0` 及已有 Torch CPU 依赖。没有另一个虚拟环境或独立服务启动命令。模型目录由
`SCHOLAR_EMBEDDING_MODELS_PATH` 指定，默认读取服务 models/ 下固定修订。

## 召回和排序

文本路将 title、TLDR、abstract 设为 3、2、1。每个逻辑字段在原字段与 `.en` 中取最高分，再由 dis_max 取最高分并加 0.1
倍其他字段分；完整标题匹配加 8，标题短语匹配加 4。文本使用 OR，不再使用 `2<70%`。学科与日期作为过滤条件。

论文向量存于 Qdrant，维度为 768，距离为 Euclid，向量与 HNSW 图均使用磁盘存储；SPECTER2 向量不做单位归一化。两路各取 200，向量检索的
`hnsw_ef` 使用 `SCHOLAR_SEARCH_NUM_CANDIDATES`，默认 1000。Qdrant 在召回时预过滤学科和提交日期：`cs.*` 等按学科组匹配，具体分类精确匹配；日期按
UTC 日起点做含边界的区间匹配。Qdrant 只返回有序 arXiv ID，Provider 再从 ES 批量回填论文并保留该顺序。arXiv provider 用 RRF k=60 融合，再按下式排序：

```text
rrf × (1 + 0.20 × citations/(citations+100)
         + 0.10 × influential_citations/(influential_citations+10))
```

缺失或零引用不加分，默认参数下总加成不超过 30%。年份不参与 V1 排序。ES 只保存关键词检索和论文事实，不保存向量；向量和分数不进入 Paper，查询响应与论文缓存排除向量。

平台通过 Nacos 的 AppSettings 控制召回成本与先验强度，配置在服务启动时生效：

| 配置                              | 默认值 | 用途                                         |
|-----------------------------------|--------|----------------------------------------------|
| SCHOLAR_SEARCH_CANDIDATES         | 200    | 文本、向量两路各自召回数量及本地候选缓存规模 |
| SCHOLAR_SEARCH_NUM_CANDIDATES     | 1000   | Qdrant HNSW 搜索宽度，不小于前项             |
| SCHOLAR_SEARCH_RRF_K              | 60     | 本地两路排名融合的 k                         |
| SCHOLAR_SEARCH_CITATION_WEIGHT    | 0.20   | 总引用加分上限                               |
| SCHOLAR_SEARCH_INFLUENTIAL_WEIGHT | 0.10   | 高影响引用加分上限                           |
| SCHOLAR_EMBEDDING_CPU_THREADS     | 2      | 查询编码使用的 Torch CPU 线程数              |

这些检索参数与模型签名一起参与候选缓存版本。固定文本权重、平滑分母、模型修订、维度和输入规则随代码发布；模型合同集中在
`src/scholar/ml/specter.py`，文本规则在 ES 仓储，融合与引用加分在 arXiv provider，不再单设 profile 文件。调整两个引用权重时，总加分上限随其和变化。

查询使用 adhoc adapter；本次存量论文使用 proximity adapter，输入为 title + SEP + abstract，CLS、512 tokens、不做单位归一化。后续每日同步只请求
S2 的 `embedding.specter_v2`。

离线输入指纹由 DuckDB 对标题、NUL 分隔符和摘要计算 SHA-256，统一表达式位于
deploy/offline/embeddings.py。续跑会复用指纹和模型签名均匹配的向量；标题或摘要改变时旧向量不再参与发布，仅更新引用数或 TLDR
时仍可复用。没有单独的 Python document_fingerprint 函数。

编码超时默认 2 秒；已知编码 HTTP/网络失败时返回文本结果，execution 标记 partial 并保留原因，该结果不写正常查询缓存。Qdrant 或 ES 检索失败会作为来源错误返回，不降级为不完整混合结果。编码恢复后的下一次请求重新执行混合检索。

搜索各来源、富化与 PDF 补救共用 `SCHOLAR_REQUEST_TIMEOUT_SECONDS` 的截止时间（默认 25 秒）。多源检索保留已完成来源；已有论文时，富化或 PDF 补救超时只记入 `enrichment_execution`，不撤销论文。调用方取消仍直接传播。检索阶段耗尽预算时，错误和日志会指出来源及正在等待的操作，例如 `arxiv qdrant.search`；取消日志还记录该操作已等待的秒数。

整次请求失败时，供应商 HTTP 错误保留实际状态；队列准入拒绝和上游限流返回 HTTP/code 429，依赖或检索截止时间超时返回 504，连接失败返回 503。S2 的 NOKEY 429 若被 KEY 成功兜底，仍正常返回成功。RPC 在重试耗尽后保留 Scholar 的完整 code/msg，不再只返回截断的 `upstream 5xx` 文本。

## 来源、凭证和缓存

CS 来源为 `[arxiv, huggingface]`，默认顺序回退，多源模式均执行。HF 搜索保留上游顺序，保留 AI 摘要、关键词、摘要模型以及渠道产物。HF
日期过滤处理前 100 个候选。

OpenAlex/HF 搜索和 HF 发现仅允许用户 BYOK；缺 key 时在缓存读取前跳过该搜索来源。lookup 和富化使用用户 key 优先、平台 key 其次。S2 同样支持用户 BYOK，用户队列按 key 隔离且不设准入阈值；平台队列仍受在线阈值保护。上游拒绝或限流不触发同来源的平台 key 重试。

HTTP 的 `X-OpenAlex-Api-Key`、`X-HuggingFace-Api-Key`、`X-S2-Api-Key` 承载 BYOK；Chat 工具配置分别使用 `openalex_api_key`、`huggingface_api_key`、`s2_api_key`
，均不进入模型参数。

来源请求批量获取候选：本地 arXiv 和 OpenAlex 各 200，HF 搜索/发现 100。候选缓存键不包含用户
limit，应用层在本次请求融合和返回时裁剪；日期、领域、排序和模型/检索版本参与缓存身份，用户标识和凭证不参与。论文事实缓存继续跨凭证共享；HF/OpenAlex 搜索和 HF 发现即使命中缓存也必须先提供 BYOK。不同有效 key 的在途 I/O 分别去重。富化只对本次返回论文执行。

离线命令与恢复操作见 [维护文档](../deploy/README.md)。真实 ES 小集验收需显式设置 `SCHOLAR_REAL_ES=1`；测试使用内存 Qdrant 和临时 DuckDB，覆盖闭区间日期过滤、学科 OR、ES 回填顺序，以及向量复用、失效和删除。

每日 S2 API 会显式请求 `embedding.specter_v2`。符合当前模型签名与标题/摘要指纹的 768 维向量持久化在 DuckDB，并发布到 Qdrant；ES 文档不包含向量。
