# 业务用例深度解析 (Application Use Cases)

WisePen Scholar Service 的业务编排集中在 `src/scholar/application/` 目录，分为 **Search（检索）**、**Lookup（精确查询）**、**Match（标题消歧匹配）**、**Discover（社区趋势发现）** 四大核心用例。

所有用例均严格管理请求总超时预算（默认 25 秒），执行非阻塞的并发或串行控制，统一调用 PDF 补救并执行最终的模型元数据投影。

---

## 一、 用例一：论文主题检索 (`PaperSearchService`)

### 1. 业务目标
为 AI Agent 提供面向研究主题、关键词、学科限定与发表时间区间的混合召回与多源融合检索。

### 2. 端到端执行流程

```mermaid
sequenceDiagram
    autonumber
    participant Client as 调用方 (MCP / HTTP)
    participant SearchUC as PaperSearchService
    participant Cache as RedisQueryCache
    participant Arxiv as ArxivProvider (Qdrant)
    participant ExtSrc as External Searcher (OpenAlex/HF)
    participant Runner as LookupRunner (通用链)
    participant Enricher as Field Enricher (HF)
    participant PDF as PaperPdfRecoverService

    Client->>SearchUC: search(query, field, dates, limit, credentials)
    SearchUC->>SearchUC: 校验各来源 BYOK 凭证是否齐备
    Note over SearchUC: 缺 Key 来源在读缓存前标记为 not_executed
    SearchUC->>Cache: 查询是否有有效候选缓存 (hash of query+field+dates)
    alt 缓存命中
        Cache-->>SearchUC: 返回未投影的候选论文列表
    else 缓存未命中
        alt 单源顺序回退模式 (default)
            SearchUC->>Arxiv: 执行 Qdrant 混合检索 (BM25 + SPECTER2)
            alt 检索成功 (含空列表)
                Arxiv-->>SearchUC: 返回候选列表 (正常结束，不再回退)
            else 发生外部 ProviderError
                SearchUC->>ExtSrc: 自动回退至次选来源 (如 OpenAlex)
            end
        else 多源并发执行模式 (multi_source)
            par 并发请求各来源
                SearchUC->>Arxiv: 本地检索
            and
                SearchUC->>ExtSrc: 第三方检索
            end
            SearchUC->>SearchUC: RRF 融合与去重
        end
        SearchUC->>Arxiv: 检索本地完整标题库 (命中则置顶加权)
        SearchUC->>Cache: 将融合候选写入 query 缓存 (TTL=600s)
    end
    SearchUC->>SearchUC: 按 limit 截断候选列表
    opt 成功来源不包含通用库
        SearchUC->>Runner: 触发通用富化短路链 (arxiv -> s2 -> openalex)
    end
    opt 领域声明了增量富化器 (如 CS 领域的 HF)
        SearchUC->>Enricher: 检查详情完整性，缺字段时补查 HF 详情
    end
    SearchUC->>PDF: 补充缺失的 PDF 阅读入口 (Unpaywall)
    SearchUC->>SearchUC: 最终模型投影 (project_paper)
    SearchUC-->>Client: 返回 SearchResult (papers + execution 报告)
```

### 3. 关键特性与设计原则
1. **BYOK 准入优先**：OpenAlex、HuggingFace 搜索必须携带用户自备凭证（BYOK）。缺少 Key 的来源在**读取缓存之前**即被判定跳过并记录为 `not_executed`；如果该领域没有可用来源，立即返回 503 并提示所需的 Key，避免非授权白嫖缓存。
2. **有序回退 vs 正常空结果**：在 `default` 模式下，来源按 `FieldPolicy.searchers` 顺序回退。**正常的空列表（0篇）视为查询成功，立刻终止**，绝不会因为搜不到就胡乱回退到次级来源导致语义漂移；仅在发生上游网络断连或 5xx 时才触发容灾回退。
3. **完整标题自动置顶**：当用户输入的内容恰好是某篇论文的完整标题时，本地库检索到的精确论文会在 RRF 融合后直接被提升至第 1 位。

---

## 二、 用例二：精准标识符查询 (`PaperLookupService`)

### 1. 业务目标
已知论文稳定标识（DOI, arXiv ID, OpenAlex ID, Semantic Scholar Corpus ID, 官方 URL 等）时，按原顺序批量检索基础论文信息并完成增量字段富化。

### 2. 内部执行核心：`LookupRunner`
`LookupRunner` 是检索后处理与精准查询共享的执行引擎：

```mermaid
flowchart TD
    Start["输入批量标识符 [ID_1, ID_2, ...]\n(1~20 项，支持重复)"] --> Norm["自动化格式归一化\n(剥离版本后缀、小写化DOI)"]
    Norm --> BatchCache["批量检查 RedisPaperCache\n(TTL=24h)"]
    BatchCache --> CheckResolved{"是否全部命中？"}
    CheckResolved -- 是 --> FieldEnrich
    CheckResolved -- 否 --> GeneralChain["进入通用来源短路链\n(默认顺序: arxiv -> s2 -> openalex)"]

    subgraph ChainLoop["通用链逐级回退"]
        ArxivLookup["1. ArxivProvider 批量查本地库"] --> ArxivHit{"是否命中？"}
        ArxivHit -- 命中项 --> MarkDone["标记该项已解决\n停止后续通用回退"]
        ArxivHit -- 未命中项 --> S2Lookup["2. S2Provider 批量查 S2 Graph"]
        S2Lookup --> S2Hit{"是否命中？"}
        S2Hit -- 命中项 --> MarkDone
        S2Hit -- 未命中项 --> OALookup["3. OpenAlexProvider 批量查 OA"]
    end

    GeneralChain --> ChainLoop
    MarkDone --> FieldEnrich["领域增量富化 (如 CS 的 HuggingFace)\n仅对字段不全的论文按需补查"]
    FieldEnrich --> RecoverPDF["PaperPdfRecoverService (补齐 PDF)"]
    RecoverPDF --> Project["统一模型出口投影 (裁剪元数据)"]
    Project --> End["按原始输入顺序和重复位置交付结果"]
```

### 3. 关键特性与设计原则
1. **通用链短路（Short-circuiting）**：一旦某个通用来源（如本地 arXiv）找到了论文基础事实，该论文就立刻被标记为已解决，**绝不会为了追求引用数或 TLDR 继续请求 S2 或 OpenAlex**，严格控制外部 API 调用成本。
2. **正负缓存一视同仁**：确认找到的论文与确认未找到（404）的标识符均采用 24 小时完整 TTL 缓存；网络断连或超时等临时错误不写缓存，避免负向污染。
3. **保序与容错交付**：返回列表 `items` 与输入的顺序完全一致；单篇论文未找到或上游超时仅标记该项状态为 `failed` 或 `not_found`，绝不因单项失败丢弃其他已成功找到的论文。

---

## 三、 用例三：完整标题消歧匹配 (`PaperMatchService`)

### 1. 业务目标
用户输入一个或多个论文完整标题（非关键词）时，精准定位到具体论文并消歧，防止 LLM 被近似检索带偏。

### 2. 三级消歧解析管线

```mermaid
flowchart LR
    Title["输入完整标题"] --> Norm["标准化预处理\n(Unicode/大小写/标点清理)"]
    Norm --> Tier1["Tier 1: Redis 标题身份索引\n(快速精确命中)"]
    Tier1 -- 未命中 --> Tier2["Tier 2: Qdrant 属性索引\n(normalized_title keyword 精确匹配)"]
    Tier2 -- 未命中 --> Tier3["Tier 3: Crossref 权威检索\n(query.bibliographic, rows=5)"]
    Tier3 --> CheckCR{"标题完全相等？"}
    CheckCR -- 是 --> GetDOI["提取权威 DOI\n调用 LookupService 补齐事实"]
    CheckCR -- 否 --> NotFound["标记为 not_found"]
    Tier1 & Tier2 & GetDOI --> AmbiguityCheck{"同名是否有歧义？"}
    AmbiguityCheck -- 多篇不同论文 --> MarkAmbiguous["状态置为 ambiguous"]
    AmbiguityCheck -- 唯一论文 --> Backfill["异步写回 Redis 标题索引"] --> Deliver["交付 Paper 实体"]
```

### 3. 关键特性与设计原则
1. **严格字符等价，禁用近似置信度**：标题匹配要求标准化后的字符必须 $100\%$ 相等，保留副标题与数学符号，严禁使用模糊编辑距离或语义相似度猜测，确保学术严肃性。
2. **并发保护**：Crossref 解析使用进程级共享的信号量 `Semaphore(2)` 控制并发，请求超时设为 5 秒，防止标题批量查询压垮公共学术基础设施。

---

## 四、 用例四：社区趋势发现 (`PaperDiscoveryService`)

### 1. 业务目标
跟踪学术社区（如 HuggingFace Daily/Weekly/Monthly Papers）的最新趋势产物、点赞排行与关联开源模型/数据集。

### 2. 执行机制与设计规范
1. **零关键词输入**：发现用例不接受 `query` 参数，完全依据社区官方频道的分发信号（Trending / Recent）进行拉取。
2. **时间周期透传**：
   - `daily`: 使用官方上游最新发布的自然日内容。
   - `weekly`: 传当周 UTC ISO 周期参数。
   - `monthly`: 传当月自然月参数。
3. **官方保序交付**：严格保持官方返回的原始热度排序，平台不根据内容日期做二次丢弃或过滤；同样通过 `PaperPdfRecoverService` 补齐 PDF 并应用紧凑投影。
4. **发现缓存**：按 `[field, period, sort, limit]` 为键写入 Redis 独立发现缓存，TTL 默认 15 分钟（900 秒）。
