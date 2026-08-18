<div align="center">

# WisePen RAG Service v2

**从权威文档出发，把检索、阅读、关系探索和证据核验连成一条可追溯的路径。**

`retrieval is an entry point · reading is deterministic · graph is navigable · evidence is verifiable`

</div>

WisePen RAG v2 是 WisePen 的文档知识服务。它不把 RAG 简化成“召回几段文本”，而是把一次回答需要的知识路径拆成四种可以独立验证的能力：先找到值得看的入口，再按结构读取原文，必要时沿知识图谱继续探索，最后回到当前 applied revision 的权威证据。

## 设计主张

`text
                         ┌────────────────────┐
                         │  applied revision  │
                         │ Markdown + ACL     │
                         └─────────┬──────────┘
                                   │ INDEX
             ┌─────────────────────┼─────────────────────┐
             │                     │                     │
          LOCATE                  READ                 EXPAND
      混合召回 + 提升          page / section       graph path + evidence
             │                     │                     │
             └──────────────┬──────┴──────────────┬──────┘
                            │                     │
                       navigation state      VERIFY
                                      SourceRef / GraphEvidence -> 原文
`

- **检索结果是阅读入口。** Qdrant 命中的 RetrievalChunk 会先经过核验，再提升成完整 ReadingBlock；模型得到的是可读正文和 Section 锚点，而不是孤立的内部 chunk。
- **确定性阅读不依赖检索。** `getPageContent` 和 `getSectionContent` 直接读取 applied revision。Section 是稳定地址，`flat_text` 也通过 synthetic Section 保留这条路径。
- **图谱结果是可导航的事实。** `expandGraph` 将领域 node/edge/path 投影成模型可读的有向路径文本，同时保留 node ID 作为后续导航锚点。
- **证据不是装饰字段。** LOCATE 用 SourceRef 核验检索入口；EXPAND 用 GraphEvidence 将关系和新节点提及直接定位到权威 Markdown 与 ReadingBlock。
- **权限和 revision 是每次读取的边界。** 资源撤权、applied revision 切换或证据不一致时，服务 fail closed，不返回看似完整的旧数据。

## 能力地图

| 能力 | 调用方要解决的问题 | 主要产物 |
| --- | --- | --- |
| `locateCandidate` | “哪些地方最值得先读？” | `state_id`、ReadingBlock、Section 锚点、图节点 |
| `getDocumentOutline` | “文档的结构是什么？” | 递归目录、`title`、`section_path`、`page_range` |
| `getPageContent` | “这一页写了什么？” | 页正文和所在 Section 锚点 |
| `getSectionContent` | “这个 Section 的直属正文是什么？” | Section 正文和 parent/previous/next/children 导航 |
| `expandGraph` | “从已发现节点还能沿哪些关系继续看？” | 路径文本、关系事实、逐 quote 证据 |

完整请求、响应示例和错误码见 [docs/API.md](docs/API.md)。

## 一个完整的阅读循环

`text
locateCandidate
      │ state_id + section_id / node_id
      ├──> getSectionContent       直接读取权威正文
      │
      └──> expandGraph             沿新节点探索
              │ relation / node evidence ReadingBlocks
              └──> getSectionContent
`

这条循环有意保留两种不同的入口：Section ID 用于确定性阅读，node ID 用于图谱导航。它们互相协作，但不互相冒充。

## API 入口

所有接口都是需要登录的内部 HTTP POST endpoint，统一使用平台响应包装 `R`。接口层负责身份、参数和错误码映射；application 层不依赖该包装。

`text
POST /internal/rag/locateCandidate
POST /internal/rag/getDocumentOutline
POST /internal/rag/getPageContent
POST /internal/rag/getSectionContent
POST /internal/rag/expandGraph
`

模型可见视图遵循几个稳定约定：页归属统一为 `page_range`；目录同时保留 `title` 和 `section_path`；确定性 Section READ 不返回检索用 ReadingBlock；图路径使用 Cypher 风格箭头，并把证据挂在逐关系 step 上。

## 结构化与安全降级

| `structure_mode` | 文档形态 | 保留能力 |
| --- | --- | --- |
| `sectioned` | 存在有效 Markdown 标题 | 标题树、页/Section READ、混合检索、contextual index、知识图谱 |
| `flat_text` | 有正文但没有可靠标题 | synthetic Section、父子分块、混合检索、Section READ |
| `empty` | 没有有效正文 | 发布空 revision，清理旧检索和图谱派生状态 |

降级发生在昂贵派生流程之前。结构不可靠时，系统不会伪造标题或图关系，但仍保留基础的定位、阅读和回源能力。

## 阅读数据模型

| 对象 | 服务对象 | 职责 |
| --- | --- | --- |
| `Section` | 导航 | 标题层级、稳定地址和阅读顺序 |
| `ReadingBlock` | LLM | 连续、完整、适合阅读的父级正文窗口 |
| `RetrievalChunk` | 检索器 | embedding、BM25、融合和 rerank 的评分单位 |
| `SourceRef` | 检索核验 | 将 RetrievalChunk 映射回权威 Markdown 与 ReadingBlock |
| `GraphEvidence` | 图谱核验 | 将关系或节点提及直接映射到权威 Markdown 与 ReadingBlock |
| graph node/relation | 探索器 | 提供可继续扩展且有原文证据的关系路径 |

搜索对象、阅读对象、导航对象和证据对象被有意分开。这是 v2 最重要的设计约束之一。

## 开发

`powershell
uv sync
uv run python -m rag.main
uv run pytest -q
uv run ruff check src tests
uv run python -m compileall -q src tests
`

Demo 会用生产 application 算法生成可审阅的文本输出：

`powershell
uv run python demo/navigation_output_demo.py
`

输出位于 `demo/*_output.txt`，适合直接附带 review 注释。

服务启动后可访问：

`text
GET /health
GET /docs
`

## 文档导航

- [API 接口说明](docs/API.md)：面向调用方的请求、响应、导航关系和错误码。

## 范围边界

RAG v2 提供可信的检索、读取、图遍历和证据事实，不替调用方生成 `page_not_found`、`section_empty` 等 Agent 文案，也不把 LLM 生成的解释当成原文证据。索引、ACL、Mongo、Qdrant、Neo4j 和 Redis 的职责边界记录在开发文档中。
