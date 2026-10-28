## 主要数据源

* 通用托管数据源采用 OpenAlex 和 Semantic Scholar（S2）

* 通用本地数据源为同步的 arxiv 数据

* 对于专用领域，则通过拓展接入额外数据源，如 AICS 会接入 HuggingFace 作为额外数据来源和富化层；其余领域按需求，选择的确有增量价值的接入

## V1 集成领域映射

* `General`

  * 涵盖学科与专业范围：跨学科、通识课程、未定领域

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`—`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`—`

  * arxiv 分类参数（`cat`）：`—`

  * 对齐策略与映射说明：**零过滤兜底**：最大化召回。

* `Computer Science`

  * 涵盖学科与专业范围：计算机、人工智能、软件工程、数据科学

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Computer Science`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`17`（`Computer Science`）

  * arxiv 分类参数（`cat`）：`cs.*`

  * 对齐策略与映射说明：三端 1:1 精确对齐。

* `Medicine`

  * 涵盖学科与专业范围：临床医学、基础医学、药学、护理、口腔

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Medicine`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`27|29|30|35|36`

    * 27：Medicine

    * 29：Nursing

    * 30：Pharmacology, Toxicology and Pharmaceutics

    * 35：Dentistry

    * 36：Health Professions

  * arxiv 分类参数（`cat`）：`—`

  * 对齐策略与映射说明：arxiv 不设临床医学预印本门类（主要分布于 medRxiv/PubMed）。

* `Biology`

  * 涵盖学科与专业范围：生物科学、遗传学、神经科学、农学生命

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Biology,Agricultural and Food Sciences`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`11|13|24|28|34`

    * 11：Agricultural and Biological Sciences

    * 13：Biochemistry, Genetics and Molecular Biology

    * 24：Immunology and Microbiology

    * 28：Neuroscience

    * 34：Veterinary

  * arxiv 分类参数（`cat`）：`q-bio.*`

  * 对齐策略与映射说明：arxiv 仅支持定量生物学（`q-bio`），涵盖计算生物、基因组学与神经回路等分支；湿实验生物学主要分布在 bioRxiv。

* `Engineering`

  * 涵盖学科与专业范围：机械、电气电子、土木、化工、能源控制

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Engineering`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`15|21|22`

    * 15：Chemical Engineering

    * 21：Energy

    * 22：Engineering

  * arxiv 分类参数（`cat`）：`eess.*`

  * 对齐策略与映射说明：arxiv 涵盖电气工程与系统科学（`eess`，含信号处理、图像音视频、控制系统）；机械土木等传统工科在 arxiv 缺乏独立大类。

* `Economics & Business`

  * 涵盖学科与专业范围：经济学、金融、管理学、工商会计

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Economics,Business`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`14|20`

    * 14：Business, Management and Accounting

    * 20：Economics, Econometrics and Finance

  * arxiv 分类参数（`cat`）：`econ.*,q-fin.*`

  * 对齐策略与映射说明：arxiv 拆分为通用经济学（`econ`）与定量金融学（`q-fin`），通过联合查询匹配商业与宏微观经济。

* `Mathematics`

  * 涵盖学科与专业范围：纯数、应用数学、数理统计、运筹优化

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Mathematics`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`26|18`

    * 26：Mathematics

    * 18：Decision Sciences

  * arxiv 分类参数（`cat`）：`math.*,stat.*`

  * 对齐策略与映射说明：arxiv 将数学与统计学独立建类，合并 `math` 与 `stat` 以完整覆盖纯数、数理统计及运筹决策分支。

* `Physics`

  * 涵盖学科与专业范围：理论物理、天文学、凝聚态、光学

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Physics`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`31`（`Physics and Astronomy`）

  * arxiv 分类参数（`cat`）：`physics.*,astro-ph.*,cond-mat.*,gr-qc,hep-ex,hep-lat,hep-ph,hep-th,math-ph,nlin.*,nucl-ex,nucl-th,quant-ph`

  * 对齐策略与映射说明：arxiv 的核心发源领域，需汇聚天体物理、凝聚态、高能粒子、量子物理及交叉物理等全部子类。

* `Chemistry`

  * 涵盖学科与专业范围：有机/无机化学、物理化学、分析化学

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Chemistry`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`16`（`Chemistry`）

  * arxiv 分类参数（`cat`）：`physics.chem-ph`

  * 对齐策略与映射说明：arxiv 无独立化学一级学科，仅收录化学物理（`physics.chem-ph`）；纯化学预印本通常在 ChemRxiv。

* `Psychology`

  * 涵盖学科与专业范围：认知心理、应用心理、脑与认知科学

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Psychology`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`32`（`Psychology`）

  * arxiv 分类参数（`cat`）：`—`

  * 对齐策略与映射说明：arxiv 无心理学独立分类（少部分计算认知多并入 `q-bio.NC` 或 `cs.AI`，不作强制映射）。

* `Social Sciences`

  * 涵盖学科与专业范围：社会学、政治学、公共管理、传播学、教育

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Sociology,Political Science,Education`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`33`（`Social Sciences`）

  * arxiv 分类参数（`cat`）：`physics.soc-ph`

  * 对齐策略与映射说明：arxiv 仅包含计算社会学与社会物理学（`physics.soc-ph`），传统社科领域在 arxiv 无对应大类。

* `Arts & Humanities`

  * 涵盖学科与专业范围：文学、历史、哲学、艺术、语言学

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Art,History,Philosophy,Linguistics`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`12`（`Arts and Humanities`）

  * arxiv 分类参数（`cat`）：`—`

  * 对齐策略与映射说明：arxiv 不覆盖人文艺术类学科。

* `Law`

  * 涵盖学科与专业范围：法学理论、民商法、国际法、刑法

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Law`

  * OpenAlex API 参数：`topics.subfield.id:3308`（降级备用：`topics.field.id:33`）

  * arxiv 分类参数（`cat`）：`—`

  * 对齐策略与映射说明：arxiv 无独立法学大类（计算法学论文多偶见于 `cs.CY`）。

* `Materials & Environmental Sciences`

  * 涵盖学科与专业范围：材料科学、地学、环境生态、气象地理

  * Semantic Scholar API 参数（`fieldsOfStudy`）：`Materials Science,Environmental Science,Geology,Geography`

  * OpenAlex API 参数（`filter=topics.field.id:...`）：`19|23|25`

    * 19：Earth and Planetary Sciences

    * 23：Environmental Science

    * 25：Materials Science

  * arxiv 分类参数（`cat`）：`cond-mat.mtrl-sci,physics.ao-ph,physics.geo-ph`

  * 对齐策略与映射说明：利用 arxiv 的交叉物理分类，抓取材料物理（`cond-mat.mtrl-sci`）、大气海洋物理（`physics.ao-ph`）与地球物理（`physics.geo-ph`）。

## 渠道定位与策略分流

Tips: 以下涉及代码均为示例性伪代码，真实实现时不应当直接照抄数据模型和实现形式

### 1. 数据源定位

系统根据数据源的覆盖范围、调用成本及独特能力，将其划分为主要召回源、补充召回源和富化源。

* **arxiv 本地索引：** 高吞吐、低延迟的本地预印本文献检索能力。支持按关键词、分类和时间范围组合查询，是未启用 OpenAlex BYOK 时的默认召回渠道。其覆盖范围受学科分类体系限制。

* **OpenAlex：** 跨学科的通用学术检索源，负责覆盖传统学术出版体系及 arxiv 未充分覆盖的领域。产品仅在用户配置有效的 OpenAlex API Key（BYOK）后启用，不默认依赖公共额度。

* **Semantic Scholar（S2）：** 提供语义检索、引用影响力、TLDR 等能力。考虑到其 API 的速率限制，默认以高价值字段富化为主；只有领域策略或当前任务明确要求时，才执行独立的 S2 搜索。

* **领域专用来源：** 根据真实增量价值接入。例如，Hugging Face Papers 可以为 AI/CS 提供趋势发现及社区信号，也可以为已有论文补充模型、数据集和代码关联信息。

不同来源的职责不要求互斥。同一个来源可以同时实现搜索和富化能力，但是否参与某次请求由检索策略决定。

### 2. 默认分流规则

系统首先判断 OpenAlex 是否已通过 BYOK 启用，再结合请求中的领域参数和该领域的 arxiv 分类映射，确定默认召回路径。

**未启用 OpenAlex BYOK 时：**

1. 未指定领域，或领域为 `General`：查询本地 arxiv 全部分类，不附加学科分类过滤。

2. 指定了明确领域，且该领域存在 arxiv 分类映射：按照映射查询相应分类。

3. 指定了明确领域，但该领域没有 arxiv 分类映射：返回明确的能力提示，不自动退化为无分类过滤的 arxiv 全库检索，以免将不匹配的结果伪装成该领域的有效覆盖。

4. 不调用 OpenAlex，也不通过未经配置的其他来源绕过上述规则。

无分类过滤的 `General` 仅表示查询整个 arxiv 本地库，并不意味着覆盖所有学科的学术文献。

**启用 OpenAlex BYOK 时：**

1. 对于配置为 OpenAlex 优先的领域，默认只调用 OpenAlex，不再自动叠加 arxiv 搜索。

2. 对于配置为 arxiv 优先的领域，默认仍以本地 arxiv 为主要召回源。

3. S2 默认承担按需富化职责。只有当缓存缺失、当前任务确实需要相关字段，或检索策略明确要求 S2 搜索时，才消耗 S2 API 额度。

4. 如果请求明确要求多源检索，则使用该领域注册的多源检索计划，而不是简单地把所有可用来源全部并发调用。

OpenAlex BYOK 的启用状态决定来源是否可用，但不自动决定每次查询都必须调用该来源。领域策略仍然负责选择默认召回渠道。

### 3. 领域策略配置

领域配置需要同时表达默认检索计划、可选的多源检索计划、arxiv 分类映射及富化需求。不能只记录一个 `primary_source`，再把多源检索留给执行器中的临时分支处理。

建议采用以下声明式结构：

```python
from dataclasses import dataclass
from enum import StrEnum


class RetrievalScope(StrEnum):
    DEFAULT = "default"
    MULTI_SOURCE = "multi_source"


@dataclass(frozen=True)
class SourceCall:
    provider: str
    limit: int = 30
    categories: tuple[str, ...] | None = None


@dataclass(frozen=True)
class EnrichmentPolicy:
    provider: str
    fields: tuple[str, ...]
    max_candidates: int = 20


@dataclass(frozen=True)
class DomainPolicy:
    # arxiv 分类映射；None 表示该领域没有可用映射
    arxiv_categories: tuple[str, ...] | None

    # 是否以 OpenAlex 作为默认召回源
    openalex_first: bool

    # 明确请求多源检索时使用的计划
    multi_source_plan: tuple[SourceCall, ...] | None

    # 与召回策略独立的富化配置
    enrichers: tuple[EnrichmentPolicy, ...] = ()
```

`multi_source_plan` 中的每个 `SourceCall` 都是实际执行的调用声明，而非仅供说明的标签。第一版可以将同一计划中的来源并发执行；如果以后确实需要有序的条件召回，再扩展为分阶段计划。

工具请求通过 `source_scope` 区分默认检索与明确的多源检索需求：

* `default`：执行领域的默认计划。

* `multi_source`：执行该领域注册的多源计划。

当某个领域没有配置多源计划时，不得自动假设它支持多源检索。系统应返回明确的能力提示，或在允许部分结果的情况下返回可用来源的结果并标记未满足的请求。

### 4. 三个领域的配置示例

以下示例仅展示策略结构。具体分类列表应直接复用领域映射表，避免在策略配置中维护第二份分类映射。

**AI/CS：arxiv 优先，支持显式多源检索及 HF 发现。**

```python
AI_CS = DomainPolicy(
    arxiv_categories=("cs.*",),
    openalex_first=False,
    multi_source_plan=(
        SourceCall(
            provider="arxiv_local",
            categories=("cs.*",),
        ),
        SourceCall(provider="openalex"),
    ),
    enrichers=(
        EnrichmentPolicy(
            provider="s2",
            fields=(
                "citations",
                "influential_citations",
                "tldr",
            ),
        ),
        EnrichmentPolicy(
            provider="hf",
            fields=(
                "upvotes",
                "models",
                "datasets",
                "code_links",
            ),
        ),
    ),
)
```

默认情况下，AI/CS 查询本地 arxiv。启用 OpenAlex BYOK 后，也不会因此自动调用 OpenAlex；只有明确要求多源检索时，才执行配置好的 arxiv + OpenAlex 计划。

S2 负责按需补充高价值学术信号，HF 除了可以富化已有候选，还通过独立的 `discover_papers` 工具提供趋势发现能力。若某次多源检索已从 S2 获取了所需字段，则不再重复请求这些字段。

**社会科学：OpenAlex 优先，arxiv 作为可选补充。**

```python
SOCIAL_SCIENCES = DomainPolicy(
    arxiv_categories=("physics.soc-ph",),
    openalex_first=True,
    multi_source_plan=(
        SourceCall(provider="openalex"),
        SourceCall(
            provider="arxiv_local",
            categories=("physics.soc-ph",),
        ),
    ),
    enrichers=(
        EnrichmentPolicy(
            provider="s2",
            fields=(
                "influential_citations",
                "tldr",
            ),
        ),
    ),
)
```

启用 OpenAlex BYOK 后，社会科学默认只查询 OpenAlex，不自动叠加 `physics.soc-ph`。只有明确要求多源检索时，才同时查询 OpenAlex 和该 arxiv 分类。

未启用 OpenAlex BYOK 时，明确指定社会科学领域的请求按照映射查询 `physics.soc-ph`，并应提示用户：该分类主要覆盖社会物理学及相关计算方向，不代表传统社会科学的完整文献覆盖。

**医学：OpenAlex 优先，不默认叠加 arxiv。**

```python
MEDICINE = DomainPolicy(
    arxiv_categories=None,
    openalex_first=True,
    multi_source_plan=None,
    enrichers=(
        EnrichmentPolicy(
            provider="s2",
            fields=(
                "influential_citations",
                "tldr",
            ),
        ),
    ),
)
```

启用 OpenAlex BYOK 后，医学默认只查询 OpenAlex，并在需要时通过 S2 补充高价值字段。

未启用 OpenAlex BYOK 时，医学没有可用的 arxiv 分类映射，因此返回明确提示，要求用户启用 OpenAlex BYOK 或调整检索范围。系统不应为了保证工具始终返回论文而查询整个 arxiv 库。

医学目前没有注册多源检索计划。未来如果接入 PubMed、临床试验注册库等具有明确增量价值的来源，再根据真实需求配置多源计划，而不是预先假设所有医学查询都应调用多个来源。

### 5. 统一执行流程

所有领域使用同一执行器，分流差异由领域配置、BYOK 状态和请求参数共同决定。

```python
async def search_papers(request):
    policy = resolve_domain_policy(request.field)
    openalex_enabled = credentials.has_openalex_key()

    # 1. 确定本次实际使用的检索计划
    if request.source_scope == RetrievalScope.MULTI_SOURCE:
        plan = policy.multi_source_plan

        if plan is None:
            return unsupported_multisource_result(request.field)

        # 显式多源计划必须经过可用性检查。
        # 不允许静默删掉来源后声称已完成多源检索。
        plan = validate_source_availability(
            plan,
            openalex_enabled=openalex_enabled,
        )

    elif openalex_enabled and policy.openalex_first:
        plan = (SourceCall(provider="openalex"),)

    elif policy.arxiv_categories is not None:
        plan = (
            SourceCall(
                provider="arxiv_local",
                categories=policy.arxiv_categories,
            ),
        )

    elif request.field in (None, PlatformField.GENERAL):
        plan = (
            SourceCall(
                provider="arxiv_local",
                categories=None,
            ),
        )

    else:
        return unsupported_domain_result(
            field=request.field,
            reason="OpenAlex 未启用，且该领域没有 arxiv 分类映射",
        )

    # 2. 按计划执行来源调用并归并候选
    papers = await execute_source_plan(plan, request)

    # 3. 按需读取缓存并补充高价值字段
    papers = await enrich_missing_signals(
        papers,
        policy.field_enrichers,
    )

    # 4. 归并结果并返回来源状态
    return build_search_result(papers)
```

以上代码为控制流示意。实际实现中，`validate_source_availability` 应明确区分必需来源与可选来源：若必需来源不可用，应拒绝完整执行或返回标记清晰的部分结果；不能把降级执行伪装成完整的多源检索。

富化阶段独立于召回计划执行。它优先使用缓存，对缺失且有实际价值的字段进行批量获取；所有 S2 请求统一经过全局 Gateway，遵守已确认的 1 RPS 限流。

### 6. 明确的设计边界

1. **BYOK 是来源可用性的前置条件。** 未配置 OpenAlex API Key 时，不调用 OpenAlex，也不依赖其公共额度。

2. **默认检索不等于多源检索。** OpenAlex 优先的领域默认只查询 OpenAlex；arxiv 优先的领域默认只查询本地 arxiv。

3. **多源检索必须可执行、可验证。** 请求通过 `source_scope="multi_source"` 表达明确意图，领域配置提供实际的来源计划，执行结果报告实际调用的来源。

4. **没有 arxiv 分类时不进行无过滤回退。** 未启用 OpenAlex 的情况下，如果明确领域没有 arxiv 分类映射，返回能力提示，不用 General 检索结果冒充该领域结果。

5. **S2 富化与搜索相互独立。** 默认先复用缓存，再按需富化；只有明确的检索计划才触发 S2 搜索。

6. **专用来源按增量价值接入。** 只有当来源能够补充现有渠道无法充分提供的覆盖、字段或发现能力时，才为对应领域注册新计划。

这套规则将领域映射、默认来源、显式多源检索和富化策略分开管理。新增领域时，可以独立定义其默认策略和可选多源计划；新增 Provider 时，只需要注册能力及更新相关计划，不需要改写通用执行器。

## 工具设计

### 1. 工具划分与职责边界

学术检索能力对 Agent 暴露三个工具，分别对应相关文献检索、已知论文解析和趋势发现三种不同的任务意图。

| 工具                | 核心职责               | 输入起点           | 典型场景                 |
| ----------------- | ------------------ | -------------- | -------------------- |
| `search_papers`   | 根据研究问题召回相关论文       | 查询文本           | 搜索某个研究方向的相关工作        |
| `lookup_papers`   | 根据精确标识符获取论文记录及所需字段 | DOI、arXiv ID 等 | 获取指定论文的引用信息、摘要或其他元数据 |
| `discover_papers` | 从可用的专用发现渠道获取趋势论文   | 发现领域及趋势条件      | 了解社区近期关注的研究          |

三个工具共享统一的论文实体模型、数据源适配器、缓存和信号富化基础设施，但不强制使用相同的执行流程。

其中，`search_papers` 和 `lookup_papers` 是通用学术能力；`discover_papers` 是按领域注册的可选能力。V1 仅为具备明确趋势发现渠道的领域开放相应能力。

工具职责遵循以下原则：

* 检索工具负责召回，不负责替 Agent 完成研究问题拆解。

* 精确查询工具负责解析已知论文，不退化为关键词搜索。

* 发现工具负责从专用渠道获取趋势，不替代常规相关性检索。

* 数据源选择由领域策略和产品配置决定，不要求 Agent 直接操作 Provider。

* 明确的多源检索需求必须通过工具参数表达，并对应真实可执行的检索计划。

### 2. `search_papers`：文献检索

#### 2.1 工具职责

根据 Agent 已经明确化的研究问题，调用当前可用的学术数据源进行定向检索，返回相关论文及其可用元数据。

工具支持关键词、领域和时间范围等常规检索条件。时间过滤直接通过参数表达，不设置固定的经典、前沿或热门分桶。

建议参数如下(也可以考虑额外提供 recency 参数路由，覆盖常见的一周内，一月内，一年内，优先级低于显式published_after/before)：

| 参数                 | 类型      | 必需 | 说明                |
| ------------------ | ------- | -- | ----------------- |
| `query`            | string  | 是  | 研究问题或检索关键词        |
| `field`            | enum    | 否  | 领域标签，默认 `general` |
| `published_after`  | date    | 否  | 发表日期下界            |
| `published_before` | date    | 否  | 发表日期上界            |
| `limit`            | integer | 否  | 期望返回数量            |
| `source_scope`     | enum    | 否  | 检索范围，默认 `default` |

`source_scope` 提供两个取值：

* `default`：执行当前领域的默认检索计划。

* `multi_source`：明确要求多源检索，执行该领域注册的多源检索计划。

`multi_source` 不是简单地把所有可用 Provider 并发调用，而是执行预先声明的来源组合。该组合可以包含多个并发来源，也可以在未来扩展为分阶段调用。

例如，AI/CS 的默认计划可以仅查询本地 arXiv；当 Agent 明确需要跨来源覆盖时，使用 `source_scope="multi_source"` 执行配置好的 arXiv 与 OpenAlex 组合。是否额外调用 S2 搜索，由该计划的声明及当前任务的实际需求决定。

#### 2.2 返回内容

每条论文记录应包含规范标识符、标题、作者、发表时间及可用的摘要、出版信息等元数据。对于已获取的引用数据、TLDR、开放获取信息和其他研究信号，保留相应的来源信息。

检索结果还应报告实际执行的来源及其状态，例如成功、缓存命中、失败或未执行。若部分来源因限流或超时未能完成，不能将结果描述为完整的全源检索。

### 3. `lookup_papers`：精确 ID 查询

#### 3.1 工具职责

该工具主要服务于论文详情获取：用户直接提供 DOI、arXiv ID 等标识符，希望了解论文内容、作者、发表信息及摘要。

#### 3.2 工具参数

建议采用以下接口：

| 参数            | 类型            | 必需 | 默认值 | 说明         |
| ------------- | ------------- | -- | --- | ---------- |
| `identifiers` | array[string] | 是  | —   | 一个或多个论文标识符 |

#### 3.3 具体执行策略

执行时基本按照 search 的约束，只是改用精确 id 查询的 api

### 4. `discover_papers`：趋势发现

#### 4.1 工具职责

从已注册的专用发现渠道中获取趋势、热门或最新发布的论文。与 `search_papers` 不同，该工具以发现渠道的分发信号为入口，而不是以研究问题的相关性为主要排序依据。

V1 可以先为 AI/CS 开放该工具，使用 Hugging Face Papers 作为发现渠道。其他领域只有在确认存在有价值的趋势发现能力后，才加入可用领域枚举。

建议参数如下：

| 参数       | 类型      | 必需 | 说明                                         |
| -------- | ------- | -- | ------------------------------------------ |
| `field`  | enum    | 是  | 当前支持发现能力的领域                                |
| `period` | enum    | 否  | 趋势窗口：`daily`、`weekly`、`monthly`、`all_time` |
| `sort`   | enum    | 否  | 排序意图：`trending`、`popular`、`recent`         |
| `query`  | string  | 否  | 可选的主题限定词                                   |
| `limit`  | integer | 否  | 返回数量，建议最大 15                               |

`field` 只允许选择当前已注册发现能力的领域。V1 可以只开放 `computer_science`，后续增加其他领域时再扩展枚举。

`period` 和 `sort` 的具体组合由能力注册表校验。不同来源可能不支持完全相同的窗口或排序方式；系统应将统一语义映射到 Provider 支持的参数，并在不支持时返回明确提示，不应静默改变排序含义。

发现结果应尽可能保留来源排名、热度指标、观测时间及可用的关联模型、数据集和代码链接。这些信号用于辅助 Agent 判断论文的近期关注度和研究产物情况，不直接等同于学术质量。

#### 4.2 与其他工具的协作

发现工具返回的结果可以作为后续研究的入口：

* 如果 Agent 需要了解某个趋势主题的相关工作，调用 `search_papers`。

* 如果 Agent 对某篇发现的论文感兴趣，需要引用信息或更完整的元数据，调用 `lookup_papers`。

* 如果发现结果已经包含所需信息，则直接使用，不重复查询。

`discover_papers` 不要求与常规学术检索自动并发执行，也不需要用户额外启用底层 Provider。Agent 根据研究任务决定是否需要趋势发现即可。

### 5. 三个工具共享的执行基础设施

三个工具可以复用以下内部组件(这里以类的形式表达，实际实现时可能为函数，有些也可能不实现)，但不要求具有相同的调用流程。

| 组件                     | 职责                         |
| ---------------------- | -------------------------- |
| `DomainPolicyRegistry` | 提供领域分类映射、默认检索计划和多源检索计划     |
| `ProviderRegistry`     | 注册各来源实现的搜索、解析、发现和富化能力      |
| `PaperResolver`        | 解析标识符并归并同一论文的跨源记录          |
| `QueryCache`           | 缓存查询候选和来源排序                |
| `PaperCache`           | 缓存论文元数据及研究信号               |
| `EnrichmentService`    | 读取缓存、批量获取缺失字段并合并信号         |
| `S2Gateway`            | 统一管理 S2 的全局限流、请求去重、排队和失败退避 |

论文实体统一使用规范标识符关联多个来源的数据。各来源的引用量和其他异构信号应保留来源及观测时间，不直接覆盖成单一数值。

对于 S2，所有搜索、解析和富化请求必须经过同一 Gateway。查询缓存和论文实体缓存相互独立，缓存命中时不消耗新的 API 请求；发生限流或来源故障时，工具应报告实际状态，而不是把部分结果伪装为完整结果。

### 6. 工具设计的约束

1. **职责单一**：检索、精确解析和趋势发现分别对应不同的任务意图。

2. **默认策略明确**：普通调用执行领域默认计划，不隐式叠加所有可用来源。

3. **多源需求显式化**：通过 `source_scope="multi_source"` 请求真实的多源计划，不能只在文档中声明支持多源检索。

4. **领域能力受约束**：没有 arXiv 分类映射时，不将无过滤的 arXiv 全库检索冒充该领域的有效覆盖；没有发现能力的领域不出现在 `discover_papers` 的可选枚举中。

5. **BYOK 边界明确**：OpenAlex 仅在 BYOK 启用时调用，不依赖公共额度。

6. **S2 按需使用**：优先复用缓存和批量富化，避免重复请求；独立搜索只在检索计划或任务意图明确要求时触发。

7. **结果可追溯**：返回论文来源、信号来源和执行状态，使 Agent 能够判断结果覆盖范围及信息缺口。

这三个工具构成学术调研的基础能力层。Agent 负责研究问题拆解、工具选择及结果综合；工具负责执行清晰、可验证的检索和信息获取任务。通过领域策略和能力注册表，系统可以在不增加通用工具复杂度的前提下，逐步扩展更多数据源和专用能力。

## arXiv 本地镜像与 S2 离线富化入库方案

### 1. 目标与设计边界

本方案用于完成 arXiv 元数据的本地镜像初始化、Semantic Scholar（S2）开源数据集的离线富化，以及富化结果向 Elasticsearch（ES）的批量发布，并支持后续定时增量更新。

整体采用以下架构：

* **arXiv：唯一的基础论文数据源。** 负责论文基础元数据、分类、发表时间及本地检索索引。

* **S2：唯一的外部富化来源。** 通过离线数据集为 arXiv 论文补充引用量、影响力引用量、TLDR、开放获取信息等可用字段，不承担论文召回职责。

* **DuckDB：离线数据处理与关联层。** 负责数据读取、规范化、跨源关联、缺失值处理、增量变更和发布数据生成。

* **Elasticsearch：在线检索与服务层。** 保存统一的论文文档，为在线检索提供关键词、分类、时间和原始学术信号过滤能力。

数据流水线不预生成经典、热门、高影响力等派生标签，不计算综合排名分数，也不把不同来源的引用数据合并为单一数值。所有论文事实以来源记录为准，未获得的字段使用 `null`。

本方案不引入 OpenAlex、Hugging Face 等其他富化来源，也不下载完整引用图、全文语料或向量数据集。后续若增加其他来源，应作为独立的数据接入需求评估，不影响当前基础数据契约。

### 2. 数据集选择与本地目录

#### 2.1 arXiv 基础快照

使用已下载的 Kaggle arXiv metadata snapshot 进行首次全量初始化。该快照通常以 JSONL 形式保存，每行对应一条论文元数据记录。

首次处理时需要检查实际文件格式与字段，包括但不限于：

* `id`：arXiv 论文 ID。

* `title`：标题。

* `abstract`：摘要。

* `authors`、`authors_parsed`：作者信息。

* `categories`：arXiv 分类。

* `doi`：DOI。

* `journal-ref`：期刊或出版引用信息。

* `versions`：版本记录。

* `update_date`：快照中的更新日期。

具体字段以实际下载版本为准。原始文件应保持不变，所有规范化和转换均在中间层完成。

#### 2.2 S2 离线数据集

优先使用同一个 S2AG release 中的以下数据集：

| 数据集      | 用途                                | 处理方式                          |
| -------- | --------------------------------- | ----------------------------- |
| `papers` | S2 标识符、外部 ID、引用量、影响力引用量及其他可用论文元数据 | 提取具有 arXiv 外部标识符的记录及需要的字段     |
| `tldrs`  | 论文的简短自然语言概括                       | 依据 `corpusId` 与已关联的 S2 论文记录连接 |

S2AG 提供按 release 发布的完整数据集，并支持通过 release 之间的差分更新本地副本。具体字段、文件数量和使用条款应以当前 release 的数据集说明为准。[S2AG Datasets API 官方文档](https://api.semanticscholar.org/api-docs/datasets)

V1 暂不需要：

* `abstracts`：arXiv 已经提供基础摘要。

* `citations`：完整引用关系不属于当前富化需求。

* `s2orc`：全文语料不属于当前需求。

* `embeddings-specter_v1` 等：当前不需要同步 S2 的向量数据。

这里的原则是只处理实际需要的字段，而不是将 S2 全量数据集直接镜像到本地。

S2 的 `paperId` 与 `corpusId` 必须区别保存：前者是 API 使用的主要论文标识符，后者是数据集及跨数据集关联中使用的重要标识符。二者不能互相替代。[S2 Academic Graph API 官方文档](https://api.semanticscholar.org/api-docs/)

#### 2.3 推荐目录结构

```text
academic-data/
├── raw/
│   ├── arxiv/
│   │   └── arxiv-metadata-snapshot.json
│   └── s2/
│       └── releases/
│           └── <release_id>/
│               ├── papers/
│               └── tldrs/
├── staging/
│   ├── arxiv/
│   └── s2/
├── warehouse/
│   └── academic.duckdb
├── exports/
│   └── es-bulk/
└── logs/
    ├── bootstrap/
    └── sync/
```

`raw/` 保存原始快照或下载的压缩分片；`staging/` 保存按需转换后的中间文件；`warehouse/` 保存 DuckDB 数据库；`exports/` 保存待发布的 ES 批次。

如果磁盘空间有限，解压后的临时文件和中间 Parquet 可以在校验通过后清理。原始快照、release 标识和处理清单应保留，以便故障恢复和追溯。

### 3. DuckDB 中间数据模型

DuckDB 不需要保存完整的 S2 论文库。只需要保留 arXiv 基础数据、与 arXiv 有关联可能的 S2 记录、所需的 TLDR，以及增量更新和发布状态。

#### 3.1 `arxiv_papers`：基础论文表

每篇 arXiv 论文一行，以规范化的 arXiv ID 作为主键。

| 字段                | 含义             |
| ----------------- | -------------- |
| `arxiv_id`        | 规范化后的 arXiv ID |
| `arxiv_id_raw`    | 原始 ID，便于追溯     |
| `title`           | 标题             |
| `abstract`        | 原始摘要           |
| `authors`         | 作者信息           |
| `categories`      | arXiv 分类列表     |
| `doi`             | DOI            |
| `journal_ref`     | 出版引用信息         |
| `submitted_date`  | 首次提交日期         |
| `updated_date`    | 可获得的更新日期       |
| `source_snapshot` | 来源快照标识         |

规范化后的 ID 用于关联，原始字段用于追溯。其他基础元数据可以根据 Kaggle 快照的实际字段继续保留，但不应在这一阶段计算学术评价指标。

#### 3.2 `s2_papers_stage`：S2 论文富化暂存表

该表只保存 S2 `papers` 数据集中与 arXiv 关联有关的记录及所需字段。

| 字段                           | 含义                |
| ---------------------------- | ----------------- |
| `s2_paper_id`                | S2 API 论文 ID      |
| `s2_corpus_id`               | S2 数据集关联 ID       |
| `arxiv_id_norm`              | 规范化的外部 arXiv ID   |
| `doi_norm`                   | 规范化的 DOI          |
| `citation_count`             | S2 引用量            |
| `influential_citation_count` | S2 影响力引用量         |
| `is_open_access`             | S2 开放获取状态         |
| `open_access_pdf_url`        | S2 提供的开放获取 PDF 链接 |
| `source_release`             | S2 数据集 release    |

实际可用字段以所下载的 `papers` schema 为准。对于数据集没有提供的字段，不从其他字段推断，也不自行计算替代值。

#### 3.3 `s2_tldrs_stage`：TLDR 暂存表

至少保留：

* `s2_corpus_id`：对应的 S2 论文标识符。

* `tldr`：S2 提供的 TLDR。

* `source_release`：来源 release。

如果一个数据集版本包含多条对应记录，应先检查其 schema 与唯一性约束，再决定如何处理。不得直接拼接重复摘要或任意覆盖不同记录。

#### 3.4 `arxiv_s2_link`：跨源关联表

该表用于维护 arXiv 论文与 S2 记录之间的实际关联，供后续富化及增量更新使用。

| 字段               | 含义                          |
| ---------------- | --------------------------- |
| `arxiv_id`       | arXiv 论文 ID                 |
| `s2_corpus_id`   | S2 数据集关联 ID                 |
| `s2_paper_id`    | S2 API 论文 ID                |
| `match_method`   | 使用的关联依据，例如精确 arXiv ID 或 DOI |
| `source_release` | 关联所依据的数据集版本                 |

这里的 `match_method` 是数据关联的技术追溯信息，不是对论文的学术评价标签。它可以留在内部关联表，不必暴露为 ES 文档中的排序字段。

#### 3.5 `etl_manifest`：任务与版本清单

记录当前数据处理进度，包括：

* 已导入的 arXiv 快照标识。

* 当前 S2 `papers` 与 `tldrs` 的 release。

* 最近一次成功处理的增量版本。

* 各阶段处理的记录数、失败数及校验结果。

* ES 发布批次与完成状态。

该表只记录数据处理状态，不与论文内容混合。

### 4. 冷启动处理流程

#### 4.1 导入并规范化 arXiv 快照

首先读取 Kaggle 原始快照，将数据导入 `arxiv_papers`。

规范化时应处理以下问题：

1. 统一 arXiv ID 格式，移除 URL 前缀、`arXiv:` 前缀和版本后缀。

2. 兼容新式与旧式 arXiv ID。

3. 保留原始 ID 和版本信息，避免规范化过程中丢失来源信息。

4. 统一日期格式、DOI 表示及分类字段。

5. 检查 ID 唯一性、空字段、异常日期和重复记录。

例如，`2501.12345v2` 与 `2501.12345v1` 应关联到同一篇基础论文，但原始版本信息不应被无条件删除。

伪代码如下：

```python
for record in read_arxiv_snapshot():
    raw_id = record["id"]
    normalized_id = normalize_arxiv_id(raw_id)

    upsert_arxiv_paper(
        arxiv_id=normalized_id,
        arxiv_id_raw=raw_id,
        title=record.get("title"),
        abstract=record.get("abstract"),
        authors=extract_authors(record),
        categories=extract_categories(record),
        doi=normalize_doi(record.get("doi")),
        source_snapshot=ARXIV_SNAPSHOT_ID,
    )
```

其中 `normalize_arxiv_id`、`normalize_doi` 等是规范化操作，不涉及任何学术价值判断。

完成后，先统计原始记录数、规范化后唯一论文数及异常记录数，再进入 S2 富化阶段。

#### 4.2 导入 S2 `papers`

从 S2 数据集下载接口获取指定 release 的 `papers` 分片及其 schema、README 和使用条款。

建议采用流式解压与处理，不要将整个 S2 数据集一次性加载到 Python 内存。可以将压缩 JSONL 转换为按字段裁剪的 Parquet，再由 DuckDB 进行批量扫描与关联。

处理逻辑：

1. 读取 S2 原始记录。

2. 提取 S2 论文 ID、Corpus ID、外部 ID、引用指标及其他实际需要的字段。

3. 从外部 ID 中提取 arXiv ID，并规范化。

4. 仅保留包含可用 arXiv ID 的关联候选。

5. 将必要字段写入 `s2_papers_stage`。

6. 保存当前 S2 release 标识。

伪 SQL：

```sql
CREATE TABLE s2_papers_stage AS
SELECT
    s2_paper_id,
    s2_corpus_id,
    normalize_arxiv_id(external_arxiv_id) AS arxiv_id_norm,
    normalize_doi(external_doi) AS doi_norm,
    citation_count,
    influential_citation_count,
    is_open_access,
    open_access_pdf_url,
    source_release
FROM s2_papers_raw
WHERE external_arxiv_id IS NOT NULL;
```

以上 SQL 使用的是规范化后的逻辑字段名，实际实现必须先根据当前 release 的 JSON schema 建立字段映射。不要假设不同 release 的字段大小写、嵌套结构和类型永远不变。

不需要保留全部 S2 论文的所有字段。只要保留当前富化需要的内容，以及未来增量更新所需的关联键和来源信息即可。

#### 4.3 导入 S2 `tldrs`

`tldrs` 通过 S2 Corpus ID 与论文关联。处理时先从 `s2_papers_stage` 提取已保留的 `s2_corpus_id`，再扫描 `tldrs` 分片，只保留这些 ID 对应的摘要。

```sql
CREATE TABLE s2_tldrs_stage AS
SELECT
    t.corpus_id AS s2_corpus_id,
    t.tldr,
    t.source_release
FROM s2_tldrs_raw AS t
SEMI JOIN (
    SELECT DISTINCT s2_corpus_id
    FROM s2_papers_stage
) AS p
ON t.corpus_id = p.s2_corpus_id;
```

此处同样是逻辑字段示意，实际字段名称以下载版本为准。

首次导入可能仍然需要扫描全部 `tldrs` 分片，因为相关记录未必按 arXiv ID 分区。优化目标是减少最终保留的数据量和后续处理成本，而不是假设可以完全跳过全量扫描。

#### 4.4 执行跨源关联

关联优先级如下：

1. **精确 arXiv ID 匹配**：作为主要关联方式。

2. **DOI 精确匹配**：只在 arXiv ID 无法直接关联时使用，并要求 DOI 唯一且不存在冲突。

3. **标题或作者模糊匹配**：V1 不自动合并。可以将候选写入独立的诊断文件，供后续人工核验。

不允许直接根据标题相似度把两条记录认定为同一篇论文，也不允许把多个 S2 记录的引用量相加。

如果一个 arXiv ID 对应多个 S2 记录，应先检查外部 ID、DOI 和其他来源字段。能够依据明确的标识符规则确定对应关系时再关联；无法确定时，保留候选记录，不将有歧义的引用指标写入最终论文文档。

伪 SQL：

```sql
CREATE TABLE arxiv_s2_link AS
SELECT
    a.arxiv_id,
    s.s2_corpus_id,
    s.s2_paper_id,
    'arxiv_id' AS match_method,
    s.source_release
FROM arxiv_papers AS a
JOIN s2_papers_stage AS s
    ON a.arxiv_id = s.arxiv_id_norm;
```

DOI 匹配作为单独的补充步骤执行，并且只接受精确、唯一、无冲突的匹配结果。关联结果应经过唯一性与歧义检查，不能简单地依靠 `DISTINCT` 隐藏冲突。

#### 4.5 生成富化结果

在关联关系确认后，将 arXiv 基础记录与 S2 富化字段合并，生成面向 ES 的最终论文文档。

```sql
CREATE TABLE paper_documents AS
SELECT
    a.arxiv_id,
    a.title,
    a.abstract,
    a.authors,
    a.categories,
    a.doi,
    a.submitted_date,
    a.updated_date,

    l.s2_paper_id,
    l.s2_corpus_id,

    s.citation_count AS s2_citation_count,
    s.influential_citation_count
        AS s2_influential_citation_count,
    t.tldr AS s2_tldr,
    s.is_open_access AS s2_is_open_access,
    s.open_access_pdf_url AS s2_open_access_pdf_url,

    s.source_release AS s2_source_release

FROM arxiv_papers AS a
LEFT JOIN arxiv_s2_link AS l
    ON a.arxiv_id = l.arxiv_id
LEFT JOIN s2_papers_stage AS s
    ON l.s2_corpus_id = s.s2_corpus_id
LEFT JOIN s2_tldrs_stage AS t
    ON l.s2_corpus_id = t.s2_corpus_id;
```

该 SQL 为结构示意。正式实现前应保证关联表与暂存表的键唯一，避免连接操作产生重复论文行。对于有歧义的匹配，不应将任意一个候选的字段写入最终文档。

**没有对应 S2 记录，或者 S2 没有提供某个字段时，该字段为&#x20;**`null`**。** 不生成 `has_s2_match`、`has_tldr` 等额外标签，也不以 `0` 或 `false` 代替未知值。

### 5. 富化数据的客观性与缺失值规范

#### 5.1 只保存来源事实

系统仅保存实际来源提供的论文信息和研究信号，不预先生成学术判断。

禁止在离线入库阶段生成：

* 经典论文、热门论文或高影响力论文标签。

* 根据发表年限计算的引用速度或热度分数。

* 按固定阈值计算的影响力等级。

* 将引用量、影响力引用量或社区信号合并后的综合分数。

* 根据是否存在代码、模型或数据集推断的可复现性标签。

如果未来的检索策略或 Agent 需要判断论文热度、影响力或研究价值，应在运行时基于实际字段与当前任务进行计算。调整排序规则不应要求重建整份基础论文库。

#### 5.2 不同来源分别保存

S2 的引用量和影响力引用量应以原始字段保存，例如：

* `s2_citation_count`

* `s2_influential_citation_count`

不从引用边数据重新计算替代值，也不根据其他来源的引用量覆盖 S2 数据。

来源标识、release 版本和获取时间应作为数据追溯信息保存。不同字段可能在不同时间更新，不应假设同一论文的所有信号都来自同一个观测时刻。

#### 5.3 缺失值处理

对于没有可用值的字段，统一记录为 `null`：

| 情况                | 保存方式            |
| ----------------- | --------------- |
| S2 未匹配到该 arXiv 论文 | S2 富化字段为 `null` |
| S2 明确提供引用量为零      | 引用量为 `0`        |
| S2 未提供引用量         | 引用量为 `null`     |
| S2 明确提供非开放获取状态    | `false`         |
| S2 未提供开放获取状态      | `null`          |
| 没有可用 TLDR         | `null`          |

`null` 表示当前没有可用的事实值，不代表对应事实为否。

对于下载失败、任务中断和待处理记录等运行状态，应记录在 ETL 清单或任务日志中，不将它们编码为论文的学术标签。

### 6. Elasticsearch 发布

#### 6.1 文档组织

每篇 arXiv 论文对应一个 ES 文档，以规范化的 `arxiv_id` 作为文档 ID。基础元数据与 S2 富化字段合并保存，不需要为在线查询再进行跨索引关联。

建议的文档结构：

```json
{
  "arxiv_id": "2501.12345",
  "title": "...",
  "abstract": "...",
  "authors": ["..."],
  "categories": ["cs.AI", "cs.LG"],
  "doi": null,
  "submitted_date": "2025-01-01",
  "updated_date": "2025-01-05",
  "s2_paper_id": null,
  "s2_corpus_id": null,
  "s2_citation_count": null,
  "s2_influential_citation_count": null,
  "s2_tldr": null,
  "s2_is_open_access": null,
  "s2_open_access_pdf_url": null,
  "s2_source_release": null
}
```

此处为结构示例，不代表实际论文记录。示例中的日期、ID 和字段值应由真实数据填充。

#### 6.2 字段映射

* `arxiv_id`、`doi`、`categories`、S2 标识符和 release 标识使用适当的 `keyword` 类型。

* `title`、`abstract`、`s2_tldr` 使用全文检索字段。

* 引用量及影响力引用量使用整数类型。

* 日期使用 `date`。

* 开放获取状态使用可空的布尔类型。

* PDF 链接按用途映射为适当的 URL 或字符串字段。

ES mapping 应显式声明，避免由动态映射推断错误的数据类型。对于可空字段，文档生成器必须保留 `null`，不能在序列化过程中擅自替换成默认值。

#### 6.3 批量写入与发布

通过 ES Bulk API 分批写入，批量大小根据文档大小、ES 写入延迟及内存使用情况调整。

首次全量导入使用独立的版本化索引。完成后检查文档数、唯一 ID、关键字段与随机抽样结果，再通过索引别名切换到正式索引，避免在线服务读取到未完成的半成品数据。

DuckDB 负责生成最终文档和发布批次；ES 只接收规范化后的论文记录，不承担跨源匹配或富化逻辑。

### 7. 后续定时增量更新

冷启动与定时更新复用同一套规范化、关联、文档生成和 ES 发布逻辑。区别仅在于输入范围为全量快照还是增量变更。

#### 7.1 arXiv 更新

Kaggle 快照只用于首次初始化。后续应使用 arXiv 官方支持的元数据增量获取机制，获取新增及更新的论文记录，避免定期重复导入整个快照。

每次更新：

1. 获取新增或更新的 arXiv 元数据。

2. 规范化 ID 并更新 `arxiv_papers`。

3. 根据当前本地 S2 关联表补充已有富化字段。

4. 生成受影响论文的最新 ES 文档。

5. 批量发布并记录完成状态。

如果新论文暂时没有 S2 记录，其 S2 字段保持 `null`，不阻塞基础论文入库。

#### 7.2 S2 更新

S2AG Datasets API 支持获取不同 release 之间的差分。更新流程应读取目标 release 的 `papers` 与 `tldrs` 差分，按数据集的主键执行新增或替换，并对删除记录执行相应删除操作。[S2AG Datasets API：增量更新说明](https://api.semanticscholar.org/api-docs/datasets)

每次任务：

1. 检查最新 release，并与 `etl_manifest` 中记录的当前版本比较。

2. 获取 `papers` 和 `tldrs` 对应的差分。

3. 更新本地 S2 暂存数据。

4. 根据变更的 S2 记录查找关联的 arXiv 论文。

5. 重新生成受影响论文的富化字段。

6. 增量发布到 ES。

7. 所有阶段成功后，才更新本地的已完成 release 标记。

若数据集版本提供删除记录，应按其真实语义处理删除，不能把删除记录当成空值更新。若 S2 记录被删除，应重新计算对应论文的可用关联；如果不再存在有效的 S2 记录，相关富化字段恢复为 `null`。

对于跨多个 release 的差分，必须按官方提供的版本链顺序处理，不能跳过中间变更而假定最新分片可以直接替代本地状态。

#### 7.3 任务状态与故障恢复

每个批次应记录输入 release、处理范围、成功数、失败数和发布状态。

任务中断后，从最后一个已完成的阶段或批次恢复。对相同数据重复执行应保持幂等，不重复生成论文文档、不累计引用量，也不重复添加任何数据。

ES 发布失败时保留待重试批次；只有在确认目标数据已成功写入后，才将对应发布状态标记为完成。涉及多阶段数据更新时，应先在 DuckDB 中完成变更与校验，再统一发布，避免将未完成的中间状态暴露给在线服务。

### 8. 数据质量与验收

冷启动完成后，至少检查以下内容：

* arXiv 规范化 ID 唯一，基础记录数量与去重统计一致。

* S2 的 `paperId` 与 `corpusId` 正确区分并保留。

* 精确 arXiv ID 关联的唯一性和歧义情况可统计。

* DOI 补充关联只使用精确、唯一、无冲突的匹配结果。

* 不同 S2 记录的引用量没有被错误相加。

* 引用量为零与引用量缺失能够区分。

* TLDR 和开放获取字段按来源原样保存。

* ES 文档数与待发布的唯一 arXiv 论文数一致。

* 随机抽样的 ES 文档与 DuckDB 生成结果一致。

* 同一增量任务重复执行不会产生重复文档或错误覆盖。

* 每条富化数据均可追溯至对应的 S2 release。

还应保留 S2 数据集随附的使用条款、版本说明及必要的来源引用。S2AG 数据集与 S2ORC 全文语料并非同一数据产品，不能将某个数据集的许可条件直接套用到其他数据集。可参阅 [S2ORC 官方说明](https://github.com/allenai/s2orc)。

### 9. V1 实施顺序

建议按以下顺序推进，确保每一步都有可验证的产物：

1. 检查 Kaggle 快照与 S2 `papers`、`tldrs` 的实际 schema，建立字段映射。

2. 在 DuckDB 中完成 arXiv 全量导入、ID 规范化与质量检查。

3. 下载并处理 S2 `papers`，仅保留具有 arXiv 关联的记录及所需字段。

4. 下载并处理 S2 `tldrs`，通过 Corpus ID 完成关联。

5. 建立关联表，处理唯一性、冲突和缺失值。

6. 生成不含派生标签的统一论文文档。

7. 全量写入版本化 ES 索引并完成抽样校验。

8. 实现 arXiv 增量更新与 S2 release 差分更新。

9. 增加任务清单、失败恢复和幂等性验证。

V1 的验收目标是形成一份**来源清晰、字段客观、可重复构建、可增量更新的 arXiv 本地论文库**。S2 在其中只承担离线元数据富化职责，不改变论文库的基础覆盖，也不在入库阶段预先决定论文的学术价值。

## S2 限流

由于 S2 的请求限流为 1 rps，必须建立全局限流和任务队列

## 缓存

对 query 级查询结果与论文查询数据做 Redis 缓存