# 一、当前架构与实现的痛点分析

在现有的 `BaseSearchTool` 与 `SearchResult` 抽象中，系统试图通过统一的入参和响应对象来屏蔽多搜索引擎的差异。但在接入 [Firecrawl](https://firecrawl.dev/)、[Exa](https://exa.ai/docs/reference/search-api-guide)、[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)、[AnySearch](https://www.anysearch.com/home)、[百度千帆](https://ai.baidu.com/ai-doc/index/AppBuilder) 以及 [TinyFish](https://agent.tinyfish.ai/) 等现代 AI 搜索引擎时，暴露出四个维度的核心设计缺陷：

### 1.1 入参接口的“最小公分母”设计引发能力阉割与配额失控

当前 `execute` 仅暴露了 `query`、`mode`、`max_results` 三个参数，上层 Agent 无法向底层传达焦点提取、时效约束等特定意图，也无法感知不同供应商在成本与质量上的真实差异，导致适配器实现要么一刀切地写死某一档位，要么完全放弃对高价值特性的利用：

* **成本与质量控制失控（Quota Mismatch）**：

  * **[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)** 的计费与检索深度强绑定：`basic` / `fast` 消耗 1 Credit，`advanced` 消耗 2 Credits。当前实现在两者之间简单粗暴地写死一档，而非针对该厂商的计费结构选定经过验证的最优默认值，导致要么无法获取深层语义切片，要么在用户简单查询时白白浪费配额。
  * **[Exa](https://exa.ai/docs/reference/search-api-guide)** 拥有从秒级响应到十秒级深度推理的完整搜索类型（`instant`、`auto`、`deep`、`deep-reasoning`），当前接口未能识别出 `auto` 才是零成本下质量最优的选择，实现上存在误选低质档位的风险。
* **高精度提取焦点（Focus/Target）被抹杀**：

  * **[Exa](https://exa.ai/docs/reference/search-api-guide)** 原生支持在 `contents.highlights` 中传入独立的 `query`（如主查“大模型优化”，抽取焦点为“显存占用与吞吐数据”）。
  * **[TinyFish](https://agent.tinyfish.ai/)** 显式设计了 `purpose` 参数（用于向检索系统解释调用本轮搜索的具体目的）。
  * 当前接口仅有单一的 `query`，使得这些引擎的二次精排与定向提取能力被完全废弃。
* **关键学术与时效约束无法下发**：

  * **[TinyFish](https://agent.tinyfish.ai/)** 支持学术年份区间限定（`pub_year_min`、`pub_year_max`）与分钟级新鲜度（`recency_minutes`）。
  * **[百度千帆](https://ai.baidu.com/ai-doc/index/AppBuilder)** 原生支持复杂的动态时间范围查询（如 `page_time: {gte: "now-1w/d"}`）与时效过滤器。当前接口缺乏约束通道，只能依赖模型在 Query 中拼写自然语言，检索命中率显著下降。

### 1.2 响应契约“表面通用、实则泄漏”的数据形态冲突

当前数据结构表面上追求统一，实际上却以特定厂商为蓝本，导致字段在各平台间表现极其分裂：

* `supplier_answer`**&#x20;的抽象泄漏**：

  * 该字段是 [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) 原生 `include_answer` 的定制产物。在接入 [Firecrawl](https://firecrawl.dev/)、[TinyFish](https://agent.tinyfish.ai/)、[百度千帆](https://ai.baidu.com/ai-doc/index/AppBuilder) 等不提供“搜索引擎端现成综述”的平台时，该字段恒为 `null`；而对于 [Exa](https://exa.ai/docs/reference/search-api-guide)（`summary`）和 [AnySearch](https://www.anysearch.com/home)，其高阶总结又无法合理映射。
* `snippet`**&#x20;与&#x20;**`highlights`**&#x20;命名体系的割裂**：

  * 当前数据模型将正文文本划分为 `snippet: str` 和 `highlights: list[str]`，这是针对 [Exa](https://exa.ai/docs/reference/search-api-guide)（切分高亮句子列表）的特定建模。
  * **[Firecrawl](https://firecrawl.dev/)** 的 [Search Highlights](https://docs.firecrawl.dev/features/search-highlights) 机制是将提取出的富文本 Markdown 片段直接覆写回 `description`。
  * **[Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search)** 是通过 `chunks_per_source` 在 `content` 中返回带有标记的段落。
  * **[百度千帆](https://ai.baidu.com/ai-doc/index/AppBuilder)** 则在 `content` 中返回最长 2000 字的正文切片。
  * 强行在 Adapter 中拼装并不存在的“高亮数组”，造成概念不对齐与下游解析负担。
* **高价值专有置信度与扩展信号被无情丢弃**：

  * **[百度千帆](https://ai.baidu.com/ai-doc/index/AppBuilder)** 针对中文网页权威性输出的 `authority_score`、`rerank_score`，以及独有的阿拉丁（Aladdin）结构化卡片与图文多模态数据完全无法传递。
  * **[Firecrawl](https://firecrawl.dev/)** 的 [Research Index](https://docs.firecrawl.dev/features/research) 携带的全局论文 ID（`paperId`、`primaryId`、`pmid`、`doi`）等元数据丢失，破坏了下游溯源链条。

### 1.3 证据级切片供给不足，导致与 Fetch 协同失衡

现存设计最隐蔽的架构痛点，在于未能正确定位 Search 与 Fetch 工具的分工边界：

* **正文切片与证据上下文（Passage-level Evidence）严重匮乏**：

  * 由于底层实现未有效激活各厂商的原生高亮段落与多 Chunks 能力，返回给 Agent 的仅有寥寥数句的 Meta Description（原生 SERP 摘要）。
* **倒逼 Agent 滥用 Fetch 工具**：

  * 面对信息密度极低的摘要，Agent 根本无法据此做出判断或完成论点验证，被迫对搜索命中的大量候选 URL 无差别调用 Fetch 工具发起二次全量抓取。
* **加剧系统延迟与开销**：

  * 这种“摘要过简 $\to$ 频繁 Fetch 全文”的恶性循环，显著拉长了端到端响应时间，并在短时间内产生大量非必要的长文本清洗计算。系统需要的是在 Search 阶段直接获得高质量的“证据级段落”，而非全量正文。

### 1.5 纯裸 HTTP 调用的脆弱性与维护成本陷阱（Protocol Fragility & High Maintenance）

当前系统倾向于通过裸写 `httpx` 手动组装 Payload 与 URL：

* **协议与 Schema 演进脱节**：

  * 各 AI 搜索服务迭代极为频繁（如字段更名、废弃、新增认证头或嵌套结构变更）。手写字典与 JSON 缺乏类型系统约束，破坏性变更只有在生产运行时报错才能暴露，缺少静态类型检查（Type Safety）。
* **重复造轮子与稳定性缺失**：

  * 厂商官方 SDK 通常内置了经过充分生产验证的网络连接池复用、签名计算、鉴权自动更新、针对 `429 Rate Limit` 的指数退避（Exponential Backoff）重试，以及精细化的错误码分类（如 Quota Exceeded vs Invalid Token）。裸写 HTTP 迫使团队在各个 Adapter 中重复编写样板代码，既脆弱又难以统一维护

---

## 二、重构预期目标与设计原则

为解决上述矛盾，重构遵循“**保持底座纯净中立，充分激发厂商壁垒特性，坚守工具正交边界**”的核心主线，确立以下设计原则与预期目标：

### 2.1 架构正交原则：严格的 Search 与 Fetch 职责解耦（Clear Boundary Decoupling）

* **Search 工具的职责终点是“高密度证据（Passage Evidences）”，严禁越界抓取全量正文**：

  * **禁止在 Search 阶段开启全网页抓取（Full-page Scraping）**：明确不使用 [Firecrawl](https://firecrawl.dev/) 的 `scrapeOptions`、[AnySearch](https://www.anysearch.com/home) 的全量 Markdown 模式或 [Tavily Search](https://docs.tavily.com/documentation/api-reference/endpoint/search) 的 `include_raw_content`。全量网页清洗与正文提取由系统自研的高效 Fetch/Read 工具闭环完成，杜绝昂贵的抓取配额浪费。
  * **隔离缓存复杂度，避免职责冲突**：全量正文通常体量巨大且具备复杂的 TTL 与版本生命周期，属于 Fetch 模块的核心职责。Search 工具必须保持轻量，返回聚焦于证据切片，避免将不可控的整页数据引入 Search 缓存体系，维护系统架构的纯粹性。
* **充分供给“证据级段落”，将 Fetch 收敛为精准按需调用**：

  * 最大化激活各平台预索引的“正文高相关切片”（如 [Exa](https://exa.ai/docs/reference/search-api-guide) Targeted Highlights、[Firecrawl](https://firecrawl.dev/) [Search Highlights](https://docs.firecrawl.dev/features/search-highlights)、[Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) Chunks、[百度](https://ai.baidu.com/ai-doc/index/AppBuilder) 2000 字片段）。
  * 使得绝大多数事实核验、多源比对在 Search 返回的证据切片内即可完成；仅在用户或 Agent 明确需要通篇研读特定文献或深度报告时，才对单篇 URL 发起 Fetch。

### 2.2 契约中立原则：意图驱动、零厂商泄漏（Zero Provider Leakage）

* **禁止专用名词下发**：在统一切面入参、出参中，严禁出现厂商特有字段（如 `aladdin`、`tavily_credits`、`exa_category`、`sub_domain`）。
* **面向 Agent 认知意图建模**：将参数语义抽象为上层智能体易于理解的检索意图：
* **检索目标与焦点（**`focus`**）**：声明“页面内核心提取对象”（如：实验对比基准、报错代码原因、核心观点）。
* **垂直领域（**`mode`**）**：规范通用网页、学术论文、开发文档等标准检索场景。
* **不暴露成本 / 深度旋钮**：契约刻意不设置任何形式的“检索深度”参数——各厂商的成本与质量权衡由 Adapter 在内部固化，不作为意图维度上抛给 Agent（详见 3.1 节的决策依据）。

### 2.3 效能对齐原则：配额透明化与壁垒特性最大化释放（Cost & Capability Alignment）

* **锁定零边际成本下的最优通道，不设可调旋钮**：

  * 各 Adapter 内部固定运行在各自厂商性价比最优的“甜点级”配置上（详见 3.2 节矩阵）：[Exa](https://exa.ai/docs/reference/search-api-guide) 恒定采用 `type="auto"` 的神经语义重排而非弱匹配的 `fast`；百度千帆与智谱恒定拉满正文切片窗口；[Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) 恒定锁定 1 Credit 的基础检索档。这些选择只在“零成本时选最优”与“唯一存在真实计费阶梯的供应商上守住入门档”之间做取舍，不向上层暴露任何可调参数，从源头杜绝无意识的配额挥霍。
* **精准对齐垂直检索内核**：

  * 学术场景下，[Firecrawl](https://firecrawl.dev/) 精准路由至 [Research Index](https://docs.firecrawl.dev/features/research)（`/v2/search/research/papers`）并提取原生论文 ID；[Exa](https://exa.ai/docs/reference/search-api-guide) 路由至 `publication` 专属索引；[TinyFish](https://agent.tinyfish.ai/) 映射 `domain_type="research_paper"`。
  * 焦点指令下发时，[Exa](https://exa.ai/docs/reference/search-api-guide) 走原生高亮抽取（Targeted Highlights），[TinyFish](https://agent.tinyfish.ai/) 映射 `purpose`，其余引擎则平滑退化拼接至 Query。

### 2.4 呈现归一原则：证据切片抽象与高保真元数据保留（Evidence Normalization）

* **统一“证据切片（Evidence Chunks）”模型**：

  * 废弃容易产生歧义的 `snippet` 与 `highlights` 独立字段二分法。
  * 统一抽象为有序的 `evidences: list[str]` 集合。无论底层提供的是富文本 Markdown 片段、高亮句子列表、语义 Chunks 还是长文切片，统一清洗汇聚为一组支撑推理的直接证据。
* **保真且扩展的元数据容器（Metadata Envelope）**：

  * 在保证 `title`、`url`、`evidences` 核心三元组干净的前提下，通过结构化 `metadata` 字典保留高价值原生信号（论文 `doi` / `pmid`、[百度](https://ai.baidu.com/ai-doc/index/AppBuilder) `authority_score` / `rerank_score`、多模态卡片等），供特定高阶 Agent 按需消费。
* **综述泛化**：

  * 将 `supplier_answer` 泛化为平台综述（`summary`），支持 [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) 的 Answer等原生 AI 概述平滑接入。

---

# 三、落地细节

## 3.1 决策：统一切面不暴露 `depth`，深度决策归还 Agent 编排层

在设计原子检索工具的入参契约时，一个自然会被提出的候选字段是“检索深度”——用一个 `fast`/`deep` 之类的旋钮，让调用方在延迟与证据密度之间做权衡。但经过对全部七家供应商底层能力与计费结构的逐一核对，这条路径被明确否决：**统一接入层不设置任何形式的深度参数，所有 Adapter 固定运行在各自厂商性价比最优的“甜点级（Sweet Spot）”配置上。**

### 3.1.1 深度分析属于 Agent 编排层，绝不属于原子工具层

* **职责边界**：第 2.1 节已确立架构红线——Search 工具是“高内聚、确定性的原子证据供给原语”。真正意义上的“深度研究”，其核心能力（任务拆解、多轮多跳检索、跨源交叉比对、反思评估、对关键文献的定向精读）天然属于上层 Agent 的认知循环，而不是单次 HTTP 调用能够包办的职责。
* **越权的代价**：把“深度”做成工具参数，等于让一次原子搜索去代理编排层的工作，模糊了 Search 与 Agent 主循环之间本应清晰的边界，也让工具的行为变得不确定——同一个 `query` 会因为一个旋钮的拨动而返回结构迥异的证据形态。

### 3.1.2 学术场景不等价于“慢与重”

大学生在学习平台上使用学术模式（`SearchMode.ACADEMIC`）时，绝大多数诉求是**精准验证与定位**：核实某篇论文的具体指标、追溯某个公式的出处、获取特定团队在 arXiv 上的最新预印本、核对 DOI 或定位官方开源实现。这类场景真正需要的是毫秒级返回的高精准文献元数据（标题、摘要、作者、DOI），而不是强行触发的“深度档”——多步扩散与二次推理不仅无助于提升命中率，反而会带来数秒级的延迟惩罚。即便是撰写文献综述这类确实需要“深”的任务，正确的落地方式也是由 Agent 连续发起多次学术模式检索并结合 Fetch 对关键文献精读，而不是依赖单次调用的某个参数去憋出一个大而全的结果。

### 3.1.3 消灭伪抽象与单厂商泄漏

逐一核对全部供应商的底层实现会发现：智谱、百度千帆、Firecrawl、TinyFish、AnySearch 五家厂商在“快”与“慢”两档之间要么完全不存在真实的成本或质量差异，要么其中一档本身就是应当被弃用的劣化选项；Exa 唯一的“深度开关”对应的是逐条加收模型调用费、且在agent平台背景下质量不升反降的摘要生成；只有 Tavily 一家的计费结构里存在真实的 1 Credit / 2 Credits 阶梯。如果仅为了适配这一家厂商的计费字段，就让整个契约层携带一个另外六家供应商全员陪跑的 `depth` 参数，将违背“契约中立、零厂商泄漏”的设计原则（2.2 节）——统一切面不应该因为个别厂商的账单结构而增加认知负担。

### 3.1.4 降低 LLM 的决策熵与 Tool Schema 开销

将 `depth` 暴露给模型，不仅增加 Tool Schema 的 Prompt Token 开销，还会让 Agent 在每次调用前多一层无谓的权衡（这次该填 `standard` 还是 `deep`），徒增参数幻觉的概率。删除该字段后，模型调用搜索工具时只需锚定三个核心意图：**查什么（**`query`**）、关注哪个指标（**`focus`**）、查全网还是查论文（**`mode`**）**，决策路径清晰、确定，不给编排层留下歧义空间。

---

## 3.2 全供应商“甜点级”最佳实践映射矩阵

基于以上决策，统一切面固化为“零边际溢价、快速响应、高密度上下文供给”的黄金参数配置，且不接受任何请求侧参数覆盖：


| 供应商                                | 甜点级配置（Sweet Spot Defaults）                                      | 计费与配额表现                      | 证据供给与设计考量                                                                     |
| --------------------------------------- | ------------------------------------------------------------------------ | ------------------------------------- | ---------------------------------------------------------------------------------------- |
| **智谱 BigModel**（平台自备默认底座） | `search_engine="search_std"content_size="high"search_intent=False`     | 单次 ¥0.01 普惠底线                | 恒定输出完整上下文切片，无时延惩罚，彻底阻断 Fetch 滥用。                              |
| **百度千帆**                          | `edition="standard"resource_type_filter=[{"type": "web"}]`             | 与`lite` 计费完全相同（约 ¥0.035） | 弃用`lite`，恒定输出最多 2000 字原文切片与权威置信度打分。                             |
| **Exa**                               | `type="auto"highlights=True`（支持 `focus` 定向抽取）`summary=False`   | 单步神经检索标准计费，无条数溢价    | 坚决关闭昂贵且信息密度更低的`summary`，依托神经重排输出高保真原文高亮。                |
| **Tavily**                            | `search_depth="fast"include_answer="basic"include_raw_content=False`   | 锁定基础 1 Credit                   | 保障亚秒级响应与核心切片，全天候附带精简速答，保护学生自备 Key 的配额。                |
| **Firecrawl**                         | 学术走`search_papers`通用走 `highlights=True`                          | 标准检索消耗，严禁全页抓取          | 通用提取 Markdown 结构段落；学术直连 PubMed/arXiv 等 ~4300 万论文图谱与完整 Abstract。 |
| **TinyFish**                          | `purpose=req.focusdomain_type="research_paper" if academic else "web"` | 标准单步消耗，不传`fetch` 参数      | 意图与微观焦点原生对齐重排，学术模式直通文献库。                                       |
| **AnySearch**                         | `tag="academic.paper" if academic else Nonemax_results <= 10`          | 支持匿名与 Key 调用，标准轻量消耗   | 保障清洗后的正文切片提取，严格按安全边界夹紧条数。                                     |

---

### 3.3 公共请求/响应参数与动态 metadata

统一接入层设计的核心原则在于：**既向智能体暴露充分的意图表达通道，又对底层异构供应商保持严格的中立与解耦。**

为了彻底杜绝供应商字段泄漏、保证接口长期演进的稳定性，请求与响应契约采用“强类型核心契约（Core Contract）+ 动态元数据信封（Dynamic Metadata Envelope）”的分层架构：

* **核心契约（一等公民）**：严格收敛为所有引擎通用、直接参与下游 Agent 认知与 Prompt 组装的高频要素。
* **动态 metadata（二等信封）**：挂载于候选条目上的灵活字典，无损透传特定厂商的高阶专有信号（如学术 DOI、权威度评分），供垂类 Agent 按需读取。

---

#### 3.3.1 公共请求参数设计（Request Contract）

入参契约遵循“表达意图，而非指示实现”的原则，统一向智能体暴露 3 个意图维度。深度不再作为可调参数暴露给上层，每个供应商的深度权衡已在 Adapter 内部按 3.2 节的甜点级配置固化完成：

```python
class SearchMode(StrEnum):
    WEB = "web"            # 通用互联网检索
    ACADEMIC = "academic"  # 学术出版物、论文预印本与科研报告


class ProviderSearchRequest(BaseModel):
    """标准检索请求契约对象"""
    query: str = Field(
        min_length=1,
        description="Concise keywords for macro-level search retrieval.",
    )
    mode: SearchMode = Field(
        default=SearchMode.WEB,
        description="Search domain scope: 'web' for general web; 'academic' for papers and preprints.",
    )
    focus: str | None = Field(
        default=None,
        description="Target entity, specific metric, or question to extract from matched pages.",  # 当前所有供应商均推荐使用目标导向的任务动宾短语/陈述句，语言需要与目标语料对齐
    )
    max_results: int = Field(
        default=10,
        ge=1,
        le=20,
        description="Maximum number of candidate evidences to return.",
    )
```

**请求字段设计意图与边界约束：**

* `query`**&#x20;与&#x20;**`focus`**&#x20;的正交分离**：

  * `query` 负责**宏观召回（Macro Retrieval）**，指示搜索引擎定位到目标网页或文献主题。
  * `focus` 负责**微观精提（Micro Extraction）**，指示底层针对页面内特定的数据指标、对比实验或核心论点进行定向提取。原生支持指令抽取的引擎直接透传；不支持的引擎由 Adapter 内部降级拼入检索词，调用方无感。
* **不设置深度参数**：

  * 按照 3.1 节的决策，每个 Adapter 内部固定运行在各自厂商性价比最优的单一配置上，不接受、也不需要请求侧的深度覆盖。若上层 Agent 确有多跳、多源交叉验证的需求，应通过连续发起多次 `execute` 调用并结合 Fetch 精读来实现，而非依赖某个隐藏的服务端参数。

---

#### 3.3.2 公共响应参数设计（Response Contract）

响应结构分为**顶层结果容器（**`WebSearchToolResult`**）与候选证据项（**`WebSearchCandidate`**）**：

```python
class WebSearchCandidate(BaseModel):
    """单条搜索候选证据（Item-level Evidence）"""
    candidate_id: str = Field(
        description="Deterministic reference label for inline citation, e.g. '[1]', '[2]'."
    )
    title: str | None = Field(
        default=None,
        description="Clean document or webpage title.",
    )
    url: str | None = Field(
        default=None,
        description="Canonical source URL for verification and inline citation.",
    )
    published_date: str | None = Field(
        default=None,
        description="Normalized ISO 8601 publication date string (YYYY-MM-DD), if available.",
    )
    evidences: list[str] = Field(
        default_factory=list,
        description="Passage-level relevant text chunks, markdown excerpts, or highlights extracted from the source.",
    )
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Dynamic envelope holding provider-specific attributes (e.g. DOI, venue, scores).",
    )


class WebSearchToolResult(BaseModel):
    """搜索工具顶层返回对象（Root-level Execution Result）"""
    query: str = Field(
        description="Normalized search query executed.",
    )
    mode: SearchMode = Field(
        description="Search scope applied for this execution.",
    )
    candidates: list[WebSearchCandidate] = Field(
        default_factory=list,
        description="Ranked candidate evidence list. Inspect URL and evidences before citing.",
    )
    summary: str | None = Field(
        default=None,
        description="Optional provider-synthesized overview. Treat strictly as an analytical lead, not a direct evidence source.",
    )
```

**响应核心字段的职责收敛：**

* **统一收拢至&#x20;**`evidences: list[str]`**：**

  * 废弃 `snippet` 与 `highlights` 的独立字段二分法。
  * 无论是富文本 Markdown 片段、高亮句子列表、语义 Chunks 还是长文切片，Adapter 一律按照“高密度切片优先，低密度 Snippet 兜底补位”的仲裁规则，清洗收拢为有序的文本列表。
* `summary`**&#x20;的语义定位**：

  * 该字段是否被填充完全取决于供应商自身能力：Tavily 在固定的甜点级配置下原生附带精简速答，恒定填充该字段；不具备端到端综述能力的供应商（百度千帆、智谱、Firecrawl、AnySearch、TinyFish）该字段恒为 `None`；Exa 的摘要归属单条结果而非全局综述，在固定配置下不生成，同样保持 `None`。
  * **契约约束**：`summary` 是提供商生成的“全景导引（Lead）”，严禁模型原样复述。

---

#### 3.3.3 动态 metadata

为了在维持核心 Schema 极简的同时，不丢失学术文献的权威标识（如 DOI、PMID、引用量）与中文搜索引擎的置信度评分，候选对象中保留了 `metadata: dict[str, Any]` 容器。

---

### 四、具体供应商适配

## 4.1 Exa 适配规范

#### 4.1.1 官方 SDK 选型与异步客户端

* **集成库**：采用官方维护的最新客户端库 `exa-py`（`pip install exa-py`）。
* **异步客户端实现**：全面使用异步客户端类 `AsyncExa`（`from exa_py import AsyncExa`）。底层基于异步 HTTP 会话管理，所有检索与切片提取均为原生 `async/await`，严禁使用同步阻塞版本 `Exa`。
* **生命周期策略**：根据 `ctx` 提取的 `api_key` 初始化或复用 `AsyncExa(api_key=...)`，避免频繁握手开销。

---

#### 4.1.2 官方高级能力与特殊参数剖析

**1. 请求侧核心参数与壁垒特性**

* `category="publication"`：[Exa POST Search](https://exa.ai/docs/reference/search) 专有学术出版物索引，覆盖 3.5 亿+ 篇论文、预印本（arXiv/bioRxiv 等）与同行评审文献，自动过滤非学术噪音并下发专属学术实体抽取管道。
* **Targeted Highlights（**`contents.highlights`**）**：

  * **定向抽取 Query**：支持在 `highlights` 字典中传入独立的 `query` 提示词，专攻页面局部的数值、指标或消融实验结论。
  * **保留官方默认长度**：缺省状态下直接沿用官方预设的上下文长度与段落切片策略，避免人为限制句数导致主谓指代残缺。
* `type`**&#x20;检索档位与固定选型**：

  * `fast`：面向交互场景的高精度极速通道（~250ms - 400ms），但会牺牲部分神经重排质量。
  * `auto`：官方默认均衡通道（~1s），深入语义召回与神经重排，与 `fast` 在计费上并无差异。
  * **决策**：适配器恒定锁定 `type="auto"`，不随请求参数变化。既然两档计费相同，就没有理由为了不存在的成本节省去牺牲召回质量。
* **排除外部工作流**：

  * 严格不启用 `deep` / `deep-reasoning`，避免外部黑盒多步循环引发超时并剥夺编排层的主权。
* **正交红线约束**：

  * 严格保持 `contents.text = False`（或不传），坚决不拉取全量网页正文。

**2. 响应侧高阶扩展信号**

* `highlights` & `highlightScores`：原生返回精准命中的正文句子列表及对应的语义置信度得分（0~1）。
* `publishedDate` & `author`：自带标准 ISO 8601 格式发布日期及作者姓名。
* `entities`**（学术实体图谱）**：当命中学术出版物时，返回包含 `venue`（发表期刊/会议）、`citationCount`（引用数）、`doi` 等深度元数据。

---

#### 4.1.3 SDK 表达与公共底座映射规范

**1. 核心高级能力 SDK 调用表达**

Adapter 内部将 `ProviderSearchRequest` 映射为 `AsyncExa.search` 的入参，保持对官方切片长度的尊重：

```python
from typing import Any
from exa_py import AsyncExa

async def call_exa_search(client: AsyncExa, req: ProviderSearchRequest):
    # 1. 检索类型：恒定使用 auto 神经语义召回（~1s），不降级为 fast，
    #    也不触发外部 deep 多步工作流
    search_type = "auto"

    # 2. 高亮配置：存在微观焦点则注入独立提取提示词，否则直接启用官方默认段落切片
    highlights_cfg: bool | dict[str, Any] = True
    if req.focus:
        highlights_cfg = {"query": req.focus}

    # 3. 计费敏感项管控：summary 逐页加收模型调用费且信息密度不如原生高亮，恒定关闭
    contents_cfg = {
        "highlights": highlights_cfg,
        "summary": False,
    }

    # 4. 执行异步检索
    return await client.search(
        query=req.query,
        num_results=req.max_results,
        type=search_type,
        category="publication" if req.mode == SearchMode.ACADEMIC else None,
        contents=contents_cfg,
    )
```

**2. 公共响应字段映射（Core Response Mapping）**

* `evidences`**&#x20;归一化（高密度切片优先）**：

  * 提取 `result.highlights` 中的切片列表作为证据：`evidences = [h.strip() for h in result.highlights if h.strip()]`。
  * 若当前页面因结构未抽取出高亮，`evidences` 保持为 `[]`，坚决不写入空白脏数据。由于请求侧已恒定关闭 `summary`，不存在可用的降级字段。
* `title` / `url` / `published_date`：
* 直接映射 `result.title`、`result.url` 与标准化的 `result.published_date`。
* `summary`**&#x20;语义定位**：

  * 由于适配器恒定请求 `summary=False`，Exa 不会返回任何单篇或全局摘要。顶层 `WebSearchToolResult.summary` 保持为 `None`。

**3. 动态&#x20;**`metadata`**&#x20;提取规则** Adapter 从 Exa 单条结果中提取专有字段，经扁平化清洗后注入 `WebSearchCandidate.metadata`：

* `author`: `result.author`（作者信息）。
* `highlight_scores`: `result.highlight_scores`（浮点数组，供下游重排参考）。
* **学术扩展提取（Academic Entities Extraction）**：

  * 遍历 `result.entities` 中类型为学术出版物（`article` / `publication`）的节点属性，提取并标准化注入：
  * `metadata["venue"]` $\leftarrow$ `notableWorks[0].venue`
  * `metadata["citation_count"]` $\leftarrow$ `notableWorks[0].citationCount`
  * `metadata["doi"]` $\leftarrow$ `notableWorks[0].doi`
  * `metadata["paper_id"]` $\leftarrow$ `result.id`

---

## 4.2 Firecrawl 适配规范

### 4.2.1 官方 SDK 选型与异步客户端

* **集成库**：采用官方维护的最新客户端库 `firecrawl-py`（`pip install firecrawl-py`）。
* **异步客户端实现**：全面使用原生异步客户端类 `AsyncFirecrawlApp`（`from firecrawl import AsyncFirecrawlApp`）。底层基于异步连接池与会话管理，所有检索与提取请求均为原生 `async/await`，坚决不使用同步阻塞版本 `FirecrawlApp`。
* **生命周期策略**：根据上下文 `ctx` 提取的 `api_key` 初始化或复用 `AsyncFirecrawlApp(api_key=api_key)`，复用底层长连接以消除重复 TLS 握手开销。

---

### 4.2.2 官方高级能力与特殊参数剖析

**1. 请求侧核心参数与壁垒特性**

* **专用学术文献索引（****[Research Index](https://docs.firecrawl.dev/features/research)****&#x20;**`/v2/search/research/papers`**）**：

  * **模式专属路由**：当 `mode == SearchMode.ACADEMIC` 时，坚决不走仅限 14 个域名的通用 `categories=["research"]`，直接接入覆盖 PubMed、bioRxiv、medRxiv 与 arXiv 的 ~4300 万论文图谱。
  * **端点支持参数**：支持 `k`（召回条数，映射 `max_results`）、`categories`（如 `cs.LG` 子类过滤）、`from`/`to` 时间区间。
* **通用网页高亮机制（****[Search Highlights](https://docs.firecrawl.dev/features/search-highlights)****）**：

  * 当 `mode == SearchMode.WEB` 时，在通用搜索中恒定开启 `highlights: True`。服务端直接将包含富文本 Markdown 语法的上下文段落就地覆写回 `description`。
  * **切片供给底线**：恒定开启高亮，不因所谓的“轻量模式”而关闭，杜绝因 Meta Description 充满 SEO 噪音而倒逼 Agent 发起全页 Fetch。
* **正交红线约束（严禁全量抓取）**：

  * 通用搜索严格保持 `scrape_options=None`（或不传），坚决不挂载全页抓取，严格守住 Search 与 Fetch 的架构职责边界。

**2. 响应侧高阶扩展信号**

* **通用 Web 搜索响应**：`description` 承载富文本 Markdown 切片；`metadata` 携带 `publishedTime` 与站点描述。
* **[Research Index](https://docs.firecrawl.dev/features/research)****文献响应**：原生返回结构化学术字段：

  * `paperId`: 平台全局唯一论文标识。
  * `primaryId`: 权威首要标识符（如 `arxiv:1706.03762`、`pmid:3421...`、`doi:10.1038/...`）。
  * `abstract`: 完整学术摘要（直接充当核心证据切片）。
  * `score`: 论文与检索意图的语义相关度打分。

---

### 4.2.3 SDK 表达与公共底座映射规范

**1. 核心高级能力 SDK 调用表达**

Adapter 内部依据 `mode` 执行精准双轨分发：

```python
from typing import Any
from firecrawl import AsyncFirecrawlApp

async def call_firecrawl_search(client: AsyncFirecrawlApp, req: ProviderSearchRequest):
    # 分支 1: 学术模式严格走专属 Research Index 论文图谱
    if req.mode == SearchMode.ACADEMIC:
        # SDK 原生 search_papers 对应 GET /v2/search/research/papers
        return await client.search_papers(
            query=req.query,
            limit=req.max_results,
        )

    # 分支 2: 通用 Web 模式走常规 search 并恒定开启 Markdown 高亮
    search_params: dict[str, Any] = {
        "limit": req.max_results,
        "highlights": True,
    }
    return await client.search(
        query=req.query,
        params=search_params,
    )
```

**2. 公共响应字段映射（Core Response Mapping）**

根据当前执行的检索分支，执行针对性的归一化映射：

* **学术模式（Research Index 分支）**：

  * `evidences`：直接使用论文的完整摘要：`evidences = [paper["abstract"].strip()]`（若存在）。Abstract 是科研论文最高浓度的结论支撑，具备极高信息密度。
  * `title` / `url`：`title = paper.get("title")`；若响应中未携带直接 Web URL，则基于权威 `primaryId` 规范化构造可访问链接（如 `[https://arxiv.org/abs/](https://arxiv.org/abs/)...` 或 `[https://doi.org/](https://doi.org/)...`）。
* `metadata`**&#x20;注入**：

  * `metadata["paper_id"]` $\leftarrow$ `paper.get("paperId")`
  * `metadata["primary_id"]` $\leftarrow$ `paper.get("primaryId")`
  * `metadata["score"]` $\leftarrow$ `paper.get("score")`
  * 从 `paper.get("sourceIds", {})` 提取并标准化注入 `doi`、`pmid`、`pmcid`。
* **通用 Web 模式（Web Search 分支）**：

  * `evidences`：优先提取已覆写在 `description` 中的 Markdown 高亮段落；高亮为空时回退原始 Meta 描述。
  * `title` / `url` / `published_date`：直接映射 `item.get("title")`、`item.get("url")` 与 `item.get("metadata", {}).get("publishedTime")`。
* `summary`**&#x20;语义定位**：

  * 两种模式下 Firecrawl 均不提供服务端端到端的大模型综述，顶层 `WebSearchToolResult.summary` 恒置为 `None`。

---

### 4.3 Tavily 适配规范

#### 4.3.1 官方 SDK 选型与异步客户端

* **集成库**：采用官方维护的最新客户端库 `tavily-python`（`pip install tavily-python`）。
* **异步客户端实现**：全面使用异步客户端类 `AsyncTavilyClient`（`from tavily import AsyncTavilyClient`）。底层封装异步 HTTP 会话，所有检索与提取请求均为原生 `async/await`，严禁使用同步阻塞版本 `TavilyClient`。
* **生命周期策略**：根据 `ctx` 提取的 `api_key` 初始化或复用 `AsyncTavilyClient(api_key=api_key)`，复用连接池以规避重复建立 TLS 握手的开销。

---

#### 4.3.2 官方高级能力与特殊参数剖析

**1. 请求侧核心参数与壁垒特性**

* **检索档位与固定选型（**`search_depth`**）**：

  * Tavily 是全部七家供应商中唯一存在真实计费阶梯的一家：`search_depth="fast"`（或官方等价字段 `basic`）消耗 1 Credit，`search_depth="advanced"` 消耗 2 Credits，用于对数十个网页做长文本解析与深层语义聚类。
  * **决策**：适配器恒定锁定入门档 `search_depth="fast"`。这是全部供应商中唯一需要真金白银权衡的地方，为保护学生自备 Key 的配额，固定守住 1 Credit 的基础检索能力，已能覆盖绝大多数通用问答场景。
* **精选摘要永远开启（**`include_answer`**）**：

  * **零 Credit 溢价**：`include_answer` 在 [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) 体系下不消耗任何额外点数，且由服务端在粗排重排时并行生成，没有理由关闭。
  * **决策**：恒定下发 `include_answer="basic"`，获取 2~3 句精简速答，与固定的 `fast` 检索档位保持一致。
* **垂直领域映射（**`topic`**）**：

  * 支持 `general`、`news`、`finance`。Tavily 本身不具备 arXiv/PubMed 等专属学术文献库，当 `mode=ACADEMIC` 时保持 `topic="general"`，不进行虚假路由。
* **正交红线约束（严禁全量抓取）**：

  * 严格保持 `include_raw_content=False`，**坚决不拉取全网页原始 Markdown/HTML**，抓取与正文清洗全部留给系统内部独立的 Fetch 链路。

**2. 响应侧高阶扩展信号**

* `answer`**（全景综述）**：顶层返回的原生综述文本，稳定供给。
* `content`**&#x20;中的语义切片（Chunked Text）**：

  * 原生返回格式为 `<chunk 1> [...] <chunk 2> [...] <chunk 3>`，每段切片最多 500 字符，由 `[...]` 分隔符清晰切分。
  * `score`**（相关性打分）**：单条结果针对 Query 的全局语义相关度浮点得分（0~1）。

---

#### 4.3.3 SDK 表达与公共底座映射规范

**1. 核心高级能力 SDK 调用表达**

Adapter 内部将 `ProviderSearchRequest` 映射为 `AsyncTavilyClient.search` 的入参，固定运行在甜点级配置上：

```python
from tavily import AsyncTavilyClient

async def call_tavily_search(client: AsyncTavilyClient, req: ProviderSearchRequest):
    # 执行异步检索：固定锁定入门检索档 + 全天候精简速答，
    # 全部七家供应商中，Tavily 是唯一存在真实计费阶梯的一家，
    # 因此恒定守住 1 Credit 的基础档，不接受任何升阶
    return await client.search(
        query=req.query,
        search_depth="fast",
        include_answer="basic",      # 零 Credit 溢价，全天候开启
        max_results=req.max_results,
        topic="general",
        include_raw_content=False,   # 坚决不开启整页抓取
    )
```

**2. 公共响应字段映射（Core Response Mapping）**

* `evidences`**&#x20;归一化（Chunk 切分与提纯）**：

  * [Tavily](https://docs.tavily.com/documentation/api-reference/endpoint/search) 的切片原生拼接在 `result.get("content")` 中，并用 `" [...] "` 显式隔开。
  * **切分管道**：Adapter 对 `content` 执行按分隔符拆解并清洗：

```python
chunks = result.get("content") or ""
evidences = [
    chunk.strip()
    for chunk in chunks.split(" [...] ")
    if chunk.strip()
]
```

* `title` / `url` / `published_date`：

  * 直接映射 `result.get("title")` 与 `result.get("url")`。
  * `published_date`：Tavily 暂未在标准 search 响应中稳定暴露发布日期，统一置为 `None`。
* `summary`**&#x20;语义定位**：

  * 直接映射顶层返回的 `response.get("answer")` 至 `WebSearchToolResult.summary`。

**3. 动态&#x20;**`metadata`**&#x20;提取规则** Adapter 从单条结果中提取专有字段，经扁平化清洗后注入 `WebSearchCandidate.metadata`：

* `metadata["score"]`: `result.get("score")`（浮点相关度得分，供下游重排器参考）。

---

## 4.4 AnySearch 适配规范

### 4.4.1 HTTP 客户端与异步会话管理

* **客户端选型**：AnySearch 官方目前专注于 MCP、Skill 及标准 RESTful 接口集成，未提供独立的 Python SDK。适配器基于现代高性能异步 HTTP 客户端 `httpx.AsyncClient` 进行封装与接入。
* **连接池与生命周期管理**：
* **单例会话复用**：在服务生命周期内维护全局或局部 `httpx.AsyncClient` 实例，复用底层 TCP/TLS 长连接，杜绝高并发检索时的频繁握手开销。
* **连接池与超时配置**：配置合理的连接池限制（`limits=httpx.Limits(max_keepalive_connections=20, max_connections=50)`）与短时延超时控制（`timeout=httpx.Timeout(10.0, connect=3.0)`）。
* **灵活鉴权策略（Flexible Authentication）**：

  * 若上下文 `ctx` 携带了 `api_key`，注入标准鉴权头 `Authorization: Bearer <API_KEY>`；

    * 若未配置或为空，AnySearch 官方原生支持**匿名访问模式**（消耗客户端 IP 的每日免费限额），Adapter 保持头信息缺省，实现免配置即刻可用。

---

### 4.4.2 官方高级能力与特殊参数剖析

**1. 请求侧核心参数与壁垒特性**

* **垂类子域标签（**`tag`**）与学术模式路由**：

  * [AnySearch POST /v1/search](https://www.anysearch.com/docs#api-endpoints) 原生提供 `tag` 参数（格式为 `{domain}.{sub_domain}`），用于指示网关直通底层垂直数据源与精排器。
  * **学术场景激活**：当 `mode == SearchMode.ACADEMIC` 时，显式传入 `tag="academic.paper"`，绕过通用泛娱乐公网，定向检索学术出版物、文献预印本与技术报告。
  * **通用场景自适应**：当 `mode == SearchMode.WEB` 时，保持 `tag=None`，交由 AnySearch 网关根据 Query 意图自适应路由与多源融合。
* **参数物理上限截断（**`max_results`**&#x20;防御）**：

  * 官方接口规范中 `max_results` 严格限制在 `1–10`。
  * 系统抽象契约的 `ProviderSearchRequest` 允许范围为 `1–20`，Adapter 在组装 Payload 时必须执行安全截断（`min(req.max_results, 10)`），防止因参数越界触发 HTTP 400 校验错误。
* **正交红线约束（格式与正文控制）**：

  * 保持默认的 `format="json"`，**坚决不传 `format="markdown"**`。官方的 Markdown 格式会将全网页抓取内容注入返回，这严重违背 Search 与 Fetch 正交解耦原则，必须阻断全页正文偷渡。

**2. 响应侧数据信号**

* `results[].content` 与 `results[].snippet`：

  * `content`：清洗后的高密度正文段落（切片级别），在信息丰富度上远高于单纯的 Meta 摘要；
  * `snippet`：常规 SERP 概览摘要，作为保底证据补充。
* **链路追踪标识**：根对象原生返回服务端生成的 UUID v4 `request_id`，用于异常排查与链路审计。

---

### 4.4.3 客户端表达与公共底座映射规范

**1. 核心检索请求调用表达（基于 httpx）**

Adapter 内部将 `ProviderSearchRequest` 组装为标准 HTTP JSON 请求并执行异步调用：

```python
from typing import Any
import httpx

MAX_EVIDENCE_CHARS = 1000  # 单条证据切片物理防御上限

async def call_anysearch_search(client: httpx.AsyncClient, req: ProviderSearchRequest) -> dict[str, Any]:
    # 1. 组装请求头：支持显式 Key 认证与匿名降级访问
    headers = {"Content-Type": "application/json"}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"

    # 2. 组装 Payload：
    # - max_results 强制夹紧至 1-10 物理安全区间
    # - 学术模式显式注入垂直 tag="academic.paper"
    # - 对 req.focus 实行 Safe No-Op，保持 query 纯净
    payload: dict[str, Any] = {
        "query": req.query,
        "max_results": min(req.max_results, 10),
    }
    if req.mode == SearchMode.ACADEMIC:
        payload["tag"] = "academic.paper"

    # 3. 异步发送请求
    resp = await client.post("https://api.anysearch.com/v1/search", json=payload, headers=headers)

    # 4. 统一异常响应捕获
    if resp.status_code != 200:
        handle_anysearch_error(resp)

    return resp.json()
```

**2. 公共响应字段映射（Core Response Mapping）**

* `evidences`**&#x20;归一化（切片优先，Snippet 降级补位与截断防御）**：

  * 优先使用清洗后的正文切片 `item.get("content")`；若缺失则降级使用 `item.get("snippet")`；
  * 若目标条目两项文本皆为空，`evidences` 保持为 `[]`，严禁注入空字符串脏数据。
* `title` / `url` / `published_date`：

  * `title = item.get("title")`
  * `url = item.get("url")`
  * `published_date = None`（AnySearch 核心检索端点暂不返回发布时间戳）。
* `summary`**&#x20;语义定位**：

  * AnySearch 定位为纯检索与数据源融合网关，不附带端到端的大模型问答生成。顶层 `WebSearchToolResult.summary` 恒设为 `None`。

**3. 动态&#x20;**`metadata`**&#x20;提取与异常对齐规则**

* **动态&#x20;**`metadata`**&#x20;提取**：

  * `metadata["sub_domain_tag"]`: 记录触发的垂直分类标签；
* **统一异常状态码转换**：


| AnySearch HTTP 状态码 | 业务错误码 (`McpErrorCode`)                    | 处理策略与说明                                      |
| ----------------------- | ------------------------------------------------ | ----------------------------------------------------- |
| **400**               | `McpErrorCode.WEB_SEARCH_INVALID_ARGUMENT`     | 请求体格式错误或缺少必填 query。                    |
| **401 / 403**         | `McpErrorCode.WEB_SEARCH_CONFIG_MISSING`       | API Key 无效、已禁用或过期。                        |
| **402**               | `McpErrorCode.WEB_SEARCH_QUOTA_EXCEEDED`       | 账户付费配额或当日免费匿名配额已耗尽。              |
| **429**               | `McpErrorCode.WEB_SEARCH_RATE_LIMIT`           | 触发 IP 或账号级并发速率限制，指示 Agent 退避重试。 |
| **502**               | `McpErrorCode.WEB_SEARCH_UPSTREAM_UNAVAILABLE` | 上游检索服务暂时不可用。                            |

---

### 4.5 百度千帆（百度搜索）适配规范

#### 4.5.1 客户端选型与异步会话管理

* **客户端选型**：直接基于 `httpx.AsyncClient` 封装，不引入任何百度云官方三方包。
* **连接池与生命周期**：复用进程级或上下文中的 `httpx.AsyncClient` 单例长连接，配置独立超时（`timeout=httpx.Timeout(10.0, connect=3.0)`）以应对公网抖动。
* **标准鉴权头**：从 `ctx` 提取 `api_key`，注入标准请求头 `Authorization: Bearer {api_key}`（亦兼容 `X-Appbuilder-Authorization`）。若未配置 Key，直接本地阻断并抛出 `McpErrorCode.WEB_SEARCH_CONFIG_MISSING`。

---

#### 4.5.2 官方高级能力与特殊参数剖析

**1. 请求侧核心参数与中文壁垒特性**

* `messages`**&#x20;结构封装**：

  * [百度搜索 API](https://ai.baidu.com/ai-doc/AppBuilder/pmaxd1hvy) 接收标准的对话列表结构 `messages: [{"role": "user", "content": "..."}]`。
  * 官方规定单轮搜索以最后一条 `role: "user"` 的文本为输入，Adapter 将抽象的 `req.query` 封装至该数组中。
* `edition`**&#x20;检索档位与彻底弃用&#x20;**`lite`：

  * 官方提供 `standard`（完整版）与 `lite`（精简版）两档。`lite` 以牺牲召回规模与精排条数为代价换取极低时延（~300ms），但官方计费口径下两档**单价完全一致**（约 ¥0.035/次）。
  * **决策**：适配器恒定锁死 `edition="standard"`，不接受任何请求侧覆盖。既然没有任何成本差异，就没有理由让用户为了不存在的时延收益去承受更差的召回质量与更短的正文切片。
* **模态资源与配额过滤（**`resource_type_filter`**）**：

  * 原生支持 `web`（网页）、`video`（视频）、`image`（图片）与 `aladdin`（阿拉丁卡片）。
  * 在标准网页检索中，将 `resource_type_filter` 显式限定为 `[{"type": "web", "top_k": req.max_results}]`，避免视频、图片等多模态噪音挤占文本配额。
* **正交红线约束**：

  * 仅对接 `/v2/ai_search/web_search` 这一纯检索端点，坚决不对接百度千帆的“智能搜索生成”等端到端生成工作流，严防 Search 边界越界。

**2. 响应侧中文高价值扩展信号**

* **2000 字正文切片（**`references[].content`**）**：单条结果直接下发多达 2000 字的高浓度原文片段，信息量远超通用 SERP 的 Meta 描述，可直接作为高质量证据切片。
* **权威性评分（**`authority_score`**）**：取值范围 $[0, 1]$ ，百度对中文政务、权威媒体、学术官网特有的权威度背书，是辨别中文垃圾营销号的杀手级特征。
* **精排相关性打分（**`rerank_score`**）**：取值范围 $[0, 1]$ ，原文片段与查询的深度语义匹配分。
* **中文元数据**：自带 `website`（站点名称）、`web_anchor`（网站锚文本）以及结构化 `date`。

---

#### 4.5.3 客户端表达与公共底座映射规范

**1. 核心检索请求调用表达（基于 httpx）**

Adapter 内部将 `ProviderSearchRequest` 映射为标准 JSON Payload 并执行异步检索：

```python
from typing import Any
import httpx

MAX_EVIDENCE_CHARS = 1000  # 单条证据切片物理防御上限，阻断长文本投毒

async def call_baidu_search(client: httpx.AsyncClient, req: ProviderSearchRequest) -> dict[str, Any]:
    # 1. 组装鉴权头（标准 Bearer 规范，无需计算复杂的 BCE 签名）
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {req.api_key}",
    }

    # 2. 废除 lite 模式：恒定采用 standard 完整版，不接受任何请求侧覆盖。
    #    彻底释放 2000 字原文切片与权威精排，且不增加任何调用账单成本
    # 3. 组装 Payload：
    # - messages: 包装为标准的单轮 user 查询
    # - resource_type_filter: 严格锁定网页资源并下发 top_k
    # - req.focus 实行 Safe No-Op，保持 query 纯净
    payload: dict[str, Any] = {
        "messages": [{"role": "user", "content": req.query}],
        "search_source": "baidu_search_v2",
        "edition": "standard",
        "resource_type_filter": [{"type": "web", "top_k": req.max_results}],
    }

    # 4. 执行异步请求
    resp = await client.post(
        "https://qianfan.baidubce.com/v2/ai_search/web_search",
        json=payload,
        headers=headers,
    )

    if resp.status_code != 200:
        handle_baidu_error(resp)

    return resp.json()
```

**2. 公共响应字段映射（Core Response Mapping）**

* `evidences`**&#x20;归一化（2000 字正文提取与硬截断防御）**：

  * 百度千帆原生提供的 2000 字正文切片（`references[].content`）已具备天然的有界性与极高信息浓度。Adapter **不再进行 人为截断**，全量透传其原文片段，充分释放百度在中文长文本事实供给上的优势。

```python
raw_content = (ref.get("content") or "").strip()

# 直接采纳百度服务端 2000 字以内的原生切片，完整保留技术论据与上下文
evidences = [raw_content] if raw_content else []
```

* 若目标条目文本为空，`evidences` 保持为 `[]`，坚决不写入空白脏数据。
* `title` / `url` / `published_date`：

  * 直接映射 `ref.get("title")` 与 `ref.get("url")`。
  * `published_date`：从 `ref.get("date")`（格式通常为 `YYYY-MM-DD HH:MM:SS`） 中提取前 10 位日期字符，标准化为 `YYYY-MM-DD`。
* `summary`**&#x20;语义定位**：

  * 百度千帆 `/v2/ai_search/web_search` 属于原子证据检索端点，不包含端到端全景综述生成。顶层 `WebSearchToolResult.summary` 恒设为 `None`。

**3. 动态&#x20;**`metadata`**&#x20;提取与异常对齐规则**

* **中文高阶元数据提取**： 从单条 reference 中清洗保留专有中文信号并注入 `WebSearchCandidate.metadata`：

  * `metadata["authority_score"]` $\leftarrow$ `ref.get("authority_score")`（[0, 1] 权威度）
  * `metadata["rerank_score"]` $\leftarrow$ `ref.get("rerank_score")`（[0, 1] 相关性）
  * `metadata["website"]` $\leftarrow$ `ref.get("website")`（站点名称）
  * `metadata["web_anchor"]` $\leftarrow$ `ref.get("web_anchor")`（锚文本）
  * `metadata["is_aladdin"]` $\leftarrow$ `ref.get("is_aladdin")`
* **统一异常状态码转换**：


| 百度 HTTP / 业务状态码 | 业务错误码 (`McpErrorCode`)                    | 处理策略与说明                               |
| ------------------------ | ------------------------------------------------ | ---------------------------------------------- |
| **400**                | `McpErrorCode.WEB_SEARCH_INVALID_ARGUMENT`     | 参数缺失或`messages` 格式校验失败。          |
| **216003 / 401**       | `McpErrorCode.WEB_SEARCH_CONFIG_MISSING`       | API Key 鉴权失败或 Header 解析错误。         |
| **402 / 欠费**         | `McpErrorCode.WEB_SEARCH_QUOTA_EXCEEDED`       | 账号免费额度耗尽且未开通按量付费。           |
| **429**                | `McpErrorCode.WEB_SEARCH_RATE_LIMIT`           | 超出账号日调用量（100,000次/天）或并发限频。 |
| **500 / 501 / 502**    | `McpErrorCode.WEB_SEARCH_UPSTREAM_UNAVAILABLE` | 百度上游服务异常或模型服务超时。             |

---

## 4.6 TinyFish 适配规范

### 4.6.1 客户端选型与异步会话管理

* **客户端选型**：[TinyFish 官方](https://agent.tinyfish.ai/)目前提供轻量、现代的 RESTful 检索服务（`GET [https://api.search.tinyfish.ai/](https://api.search.tinyfish.ai/)`），未单独提供 Python SDK。适配器全面基于 `httpx.AsyncClient` 进行原生异步封装。
* **连接池与生命周期管理**：

  * 复用服务生命周期内的单例 `httpx.AsyncClient`，长连接复用降低 TCP/TLS 握手延迟；
  * 配置合理的超时预算：`timeout=httpx.Timeout(10.0, connect=3.0)`。
* **专属鉴权头**：

  * TinyFish 采用自定义请求头鉴权：`X-API-Key: {api_key}`；
  * 若上下文 `ctx` 未能提取到有效 Key，在发起请求前直接本地阻断并抛出 `McpErrorCode.WEB_SEARCH_CONFIG_MISSING`。

---

### 4.6.2 官方高级能力与特殊参数剖析

**1. 请求侧核心参数与壁垒特性**

* **意图增强参数（**`purpose`**）与微观焦点原生对齐**：

  * 官方原生提供 `purpose` 字符串参数（1~2000 字符），官方定义为 *“Why this search is being run — the underlying goal or task the results will be used for”*，用于指示检索与精排系统对齐任务深层目标。
  * **契约对齐**：公共契约的微观焦点 `req.focus` 是极佳的任务目标描述，Adapter 直接将其透传给 `purpose`，激活服务端的意图引导与二次语义重排，**无需做任何字符串降级拼接**。
* **垂直领域模式路由（**`domain_type`**）**：

  * 原生支持枚举：`"web"`、`"news"`、`"research_paper"`。
  * **学术模式路由**：当 `mode == SearchMode.ACADEMIC` 时，显式下发 `domain_type="research_paper"`，将检索范围定向限制在学术出版物与预印本库中；
  * **通用模式路由**：当 `mode == SearchMode.WEB` 时，保持默认 `domain_type="web"`。
* **分页与数量机制**：

  * 接口通过 `page` 参数（0~10）进行结果分页，默认单页返回 Top-10 结果；
  * 适配器默认请求首屏 `page=0`，若 `req.max_results` 小于返回条数，由 Adapter 在内存层做切片截断（`results[:req.max_results]`）。
* **正交红线约束（严禁全量抓取）**：

  * TinyFish 原生提供了 `fetch` 参数（JSON 编码字符串）以支持在搜索端点就地抓取页面内容。
  * **坚决不传&#x20;**`fetch`**&#x20;参数**（保持缺省）：严格守住架构原则中 Search 与 Fetch 的正交解耦边界，禁止在检索阶段开启整页抓取，杜绝抓取配额浪费与不可控长文本对 Search 缓存的污染。

**2. 响应侧高阶扩展信号**

* `results[].snippet`：服务端提取的与 Query 及 Purpose 相关的上下文摘要段落。
* `results[].site_name`：站点官方名称（如 `"arxiv.org"`、`"github.com"`），有助于识别权威域名。
* `results[].position`：服务端精排绝对位置序号（从 1 开始）。
* `request_id`：请求链路追踪标识符，用于错误溯源与审计。

---

### 4.6.3 客户端表达与公共底座映射规范

**1. 核心检索请求调用表达（基于 httpx）**

Adapter 内部将 `ProviderSearchRequest` 映射为标准 GET 请求参数并执行异步调用：

```python
from typing import Any
import httpx

async def call_tinyfish_search(client: httpx.AsyncClient, req: ProviderSearchRequest) -> dict[str, Any]:
    # 1. 组装鉴权头：使用官方标准 X-API-Key 请求头
    headers = {
        "X-API-Key": req.api_key,
    }

    # 2. 组装 Query 参数（GET 请求）：
    # - query: 宏观检索词
    # - purpose: 将 req.focus 完美映射为意图说明，激活精排对齐
    # - domain_type: 学术模式定向锁定 research_paper
    # - 坚决不传 fetch 参数，阻断整页抓取
    params: dict[str, Any] = {
        "query": req.query,
    }
    if req.focus:
        params["purpose"] = req.focus

    if req.mode == SearchMode.ACADEMIC:
        params["domain_type"] = "research_paper"

    # 3. 异步发送 GET 检索请求
    resp = await client.get(
        "https://api.search.tinyfish.ai/",
        params=params,
        headers=headers,
    )

    # 4. 统一异常捕获与错误转换
    if resp.status_code != 200:
        handle_tinyfish_error(resp)

    return resp.json()
```

**2. 公共响应字段映射（Core Response Mapping）**

* `evidences`**&#x20;归一化（Snippet 提纯与保底）**：

  * TinyFish 在 `results[].snippet` 中下发相关的段落摘要；
  * Adapter 将其提取并清洗为证据列表：

```python
snippet = (item.get("snippet") or "").strip()
evidences = [snippet] if snippet else []
```

* 若目标条目 `snippet` 为空，`evidences` 保持为空列表 `[]`，坚决不写入空白脏数据。
* `title` / `url` / `published_date`：

  * 直接映射 `item.get("title")` 与 `item.get("url")`；
  * `published_date`：TinyFish 通用结果中未暴露具体发布日期，统一置为 `None`。
* `summary`**&#x20;语义定位**：

  * TinyFish 属于纯净的原子证据检索端点，不提供端到端的大模型综述。顶层 `WebSearchToolResult.summary` 恒置为 `None`。

**3. 动态&#x20;**`metadata`**&#x20;提取与异常对齐规则**

* **动态&#x20;**`metadata`**&#x20;提取**： 从单条结果中保留特色字段注入 `WebSearchCandidate.metadata`：

  * `metadata["site_name"]` $\leftarrow$ `item.get("site_name")`
  * `metadata["position"]` $\leftarrow$ `item.get("position")`
  * 顶层 `execution_metadata` 提取 `resp_json.get("request_id")` 供链路追踪。
* **统一异常状态码转换**：


| TinyFish HTTP 状态码 | 业务错误码 (`McpErrorCode`)                    | 处理策略与说明                            |
| ---------------------- | ------------------------------------------------ | ------------------------------------------- |
| **400**              | `McpErrorCode.WEB_SEARCH_INVALID_ARGUMENT`     | 检索词超长（>2000字符）或参数格式不合法。 |
| **401 / 403**        | `McpErrorCode.WEB_SEARCH_CONFIG_MISSING`       | API Key 缺失、未激活或无权访问。          |
| **402**              | `McpErrorCode.WEB_SEARCH_QUOTA_EXCEEDED`       | 账户余额不足或超出调用限额。              |
| **429**              | `McpErrorCode.WEB_SEARCH_RATE_LIMIT`           | 触发调用频次限制，提示上层指数退避。      |
| **500 / 503**        | `McpErrorCode.WEB_SEARCH_UPSTREAM_UNAVAILABLE` | TinyFish 服务端异常或上游无响应。         |

---

## 五、平台搜索源接入

### 5.1 痛点剖析与自备底座的必要性

在面向大学生的 AI 学习与知识平台中，检索基础设施面临着严峻的“三元冲突”：**学生认知门槛、检索信噪比与系统 Token 经济性**。

* **免费公网爬虫方案（DDG / Fourget）体验崩溃**：

  * 平台此前接入的 DuckDuckGo 与 Fourget 属于轻量 HTML 爬虫代理，返回的 `snippet` 充斥着连续省略号 `...`、网页侧边栏与版权乱码，本质上是面向人类快速扫视的粗糙 Meta 描述。
  * **恶性循环**：信息密度极低直接剥夺了模型的推理依据，倒逼 Agent 对搜索命中的候选 URL 频繁发起二次 Fetch 全页抓取。一次简单查证演变成“1 次搜索 + 多次网页渲染抓取”，端到端延迟飙升，且大量脏数据严重污染上下文窗口。
* **纯 BYOK（Bring Your Own Key）模式的落地落空**：

  * 相当一部分非计算机专业学生（文、史、哲、经管、医学等）不具备 API、Token、跨国支付绑定等概念。
  * 强制推行 BYOK 会导致平台基础的“联网搜索”功能使用率低下，丧失产品竞争力。
* **平台自备托底（Platform-Managed）的必然性**：

  * 参照 Claude、Kimi等产品的成熟范式，平台必须自备高确定性、针对 LLM 优化的高密度检索源，并将 API 成本折算为虚拟 Token 转嫁给用户，实现全员“零门槛开箱即用”。

---

### 5.2 市场主流搜索源横向对比与选型评估

根据实际调研与平台用户画像评估，针对大学生课业场景的核心候选方案对比如下：


|                                               |                                          |                                                 |                                                            |                                                  |                                                                                           |
| ----------------------------------------------- | ------------------------------------------ | ------------------------------------------------- | ------------------------------------------------------------ | -------------------------------------------------- | ------------------------------------------------------------------------------------------- |
| **候选供应商**                                | **单次硬成本**                           | **等效 Token 消耗(按 ¥1.5/M Tokens 换算)**     | **语料匹配度(课业/概念/学术/代码)**                        | **证据形态与 LLM 友好度**                        | **选型结论与定性**                                                                        |
| **海外 AI 引擎** (Exa / Tavily / Firecrawl)   | **¥0.035 ~ ¥0.07** ($0.005 ~ $0.01)    | **约 25,000 ~ 50,000** (扣减过重，学生无法承受) | 极高（海外顶会、GitHub、arXiv、官方英文文档）              | 极高（段落切片、Markdown 结构、代码高亮）        | **仅作高级 BYOK** 自备转嫁成本过高，多轮问答直接掏空学生额度。                            |
| **火山引擎** (字节跳动 Web Search)            | **¥0.006** (千次 6 元，极低)            | **约 4,000** (学生完全无感知)                   | 弱（主打头条资讯、抖音生活、大众热点）                     | 中等（提供基础摘要与时效切片）                   | **淘汰** 社科通识勉强可用，但理科原理、公式定理及技术文档噪音极大。                       |
| **百度千帆** (Web Search V2)                  | **约 ¥0.035** (千次 35 元，日免 100 次) | **约 23,000** (转嫁压力偏大)                    | 强（中文政企、国内高校、百度百科、阿拉丁卡片）             | 极高（原生下发最多 2000 字高浓度正文原文切片）   | **不宜作为默认托底** 单价是智谱的 3.5 倍，日免 100 次对多租户杯水车薪，成本转嫁劣势明显。 |
| **博查 AI (Bocha)** (国内版 Tavily 定位)      | **¥0.036** (千次 36 元)                 | **约 24,000**                                   | 极强（Bing 全球开发者与技术文档深度清洗）                  | 极高（原生 Markdown 切片与代码块保留）           | **适合高阶备选** 工程体验极佳，但成本相对普惠场景偏贵。                                   |
| **智谱 BigModel** (Web Search - `search_std`) | **¥0.010** (千次 10 元，单次 1 分钱)    | **约 6,600 ~ 8,000** (极为普惠，体感轻微)       | **极强**（清华系背景，学术概念、中文百科、学科通识极扎实） | **极高**（原生支持段落级切片管控与多级内容输出） | **首选默认托底方案** 成本、语料与模型亲和度达到最佳平衡点。                               |

---

### 5.3 选型结论：为什么锁定智谱 Web Search (`search_std`)

1. **绝对可控的 Token 转嫁成本（1 分钱普惠防线）**：

* 基础版单次调用仅需 **¥0.01**。按平台算力折算，单次调用仅对应约 **~7,000 Tokens**。
* 学生完成一次涉及 2~3 轮搜索的综合问答，总计扣减仅 2 万 Token 左右，完全在学生的日常心理承受阈值内，不会产生“搜几下额度就蒸发”的负反馈。

2. **平坦化固定策略：不设置可调节维度**：

* **拒绝人为制造“切片断层”**：智谱服务端的 `content_size`（`medium` 与 `high`）只是正文截取窗口大小的区别，接口时延与费用完全一致。若刻意降为 `medium`，关键公式、代码前置条件极易被腰斩，反而诱发昂贵的二次 Fetch；
* **规避不可控的成本与时延膨胀**：若字面接入 `search_pro`（高阶引擎），调用单价将暴增 3~5 倍且耗时大幅抖动，破坏固定 Token 转嫁模型。
* **决策**：适配器恒定锁死 `search_engine="search_std"` 与 `content_size="high"`，不接受任何请求侧覆盖，一次性提供最大上下文，兼顾极限低成本与高确定性。

3. **抽取式高密度切片终结 Fetch 滥用**：

* 智谱返回的 `content` 经过段落级提取与去噪，主谓宾结构完整、事实闭环，直接作为 `evidences` 供给下游大模型。
* 80% 以上的学科概念解析、定义比对与事实核验在 Search 阶段直接闭环，彻底终结了爬虫抓取的长延迟和高开销。

4. **聚焦 Agent 纯净检索（显式禁用&#x20;**`search_intent`**）**：

* 在现代 Agent 编排体系下，口语化提问的去噪与检索词重写由上层 LLM 自身直接消化完成。
* 适配器显式配置 `search_intent=False`，跳过服务端的二次黑盒意图分析，避免双重改写带来的语义漂移，缩短检索端到端时延。

5. **学科通识与学术语料的高信噪比**：

* 针对教材知识点、学术综述、考研考公通识与权威中文百科，智谱底层整合了自研学术索引及搜狗/夸克语料，过滤了泛娱乐与营销号噪音，深度契合大学生的课业与科研需求。

---

### 5.4 架构决策与基于 `httpx.AsyncClient` 的纯异步接入规范

#### 5.4.1 架构决策：坚决不引入官方 SDK（`zai-sdk`）

智谱开放平台虽然提供了 `zai-sdk`，但在高并发异步服务底座中，**坚决不引入该 SDK，全面基于&#x20;**`httpx.AsyncClient`**&#x20;原生异步接入**：

* **根治事件循环阻塞与线程池饥饿**：

  * `zai-sdk` 底层为同步阻塞 I/O，缺乏原生 `async/await` 支持。
  * 若在异步框架中使用 `asyncio.to_thread` 包装，在并发检索时，会产生剧烈的线程上下文切换与线程池排队等待，极易引发线程饥饿，拖慢主事件循环。
* **杜绝重型依赖污染与版本冲突**：

  * `zai-sdk` 绑定了智谱全量模型生态的依赖网。平台仅调用其独立的 Web Search 检索接口，引入全家桶 SDK 会带来严重的依赖冗余，极易与平台已有的 Pydantic 和 HTTP 工具链产生版本锁死冲突。

#### 5.4.2 客户端表达与请求契约映射

在适配器实现中，保持 Query 由上层 Agent 纯净表达，不设置任何可调节的深浅分级，恒定输出最大化上下文段落：

```python
from typing import Any
import httpx

ZHIPU_SEARCH_URL = "https://open.bigmodel.cn/api/paas/v4/web_search"

async def call_zhipu_search(client: httpx.AsyncClient, req: ProviderSearchRequest) -> dict[str, Any]:
    # 1. 组装标准 Bearer 鉴权头
    headers = {
        "Authorization": f"Bearer {req.api_key}",
        "Content-Type": "application/json",
    }

    # 2. 组装 Payload：
    # - search_engine: 恒定锁定 search_std (¥0.01/次)，坚决不调用高成本且时延抖动的 search_pro
    # - content_size: 恒定锁定 high，最大化切片证据上下文，不接受任何请求侧覆盖
    # - search_intent: 显式置为 False，由上层 Agent 保证检索词质量，跳过服务端二阶段改写
    # - search_query: 官方建议 <= 70 字符，执行安全防御截断
    # - req.focus: 实行 Safe No-Op，保持 Query 纯净
    payload: dict[str, Any] = {
        "search_query": req.query[:70],
        "search_engine": "search_std",
        "content_size": "high",
        "count": min(max(req.max_results, 1), 50),
        "search_intent": False,
    }

    # 3. 异步请求与状态校验
    resp = await client.post(ZHIPU_SEARCH_URL, json=payload, headers=headers)
    if resp.status_code != 200:
        handle_zhipu_error(resp)

    return resp.json()
```

#### 5.4.3 公共响应字段映射（Core Response Mapping）

* `evidences`**&#x20;归一化**：

  * 直接提取结构化清洗后的高密度正文段落 `item.get("content")`；
  * 若文本非空，直接注入证据列表：`evidences = [content.strip()]`；若为空则保持 `[]`，严禁写入脏数据。
* `title` / `url` / `published_date`：

  * `title = item.get("title")`
  * `url = item.get("link")`（智谱返回的网页链接字段为 `link`）
  * `published_date`：提取 `item.get("publish_date")` 并标准化为 `YYYY-MM-DD`。
* `summary`**&#x20;语义定位**：

  * 智谱 Web Search API 定位为纯原子检索，不包含跨源端到端综合概述生成。顶层 `WebSearchToolResult.summary` 恒置为 `None`。
* **动态&#x20;**`metadata`**&#x20;提取**：

  * `metadata["media"]` $\leftarrow$ `item.get("media")`（权威媒体/站点名称）

---

### 5.5 **双通道保留**：

* **学生默认通道**：平台全托管的智谱 `search_std`，零配置、开箱即用；
* **高级通道**：在“高级设置”中保留 BYOK，供有深度海外文献调研需求的用户配置个人 Exa / Firecrawl API Key，免扣平台 Token。
