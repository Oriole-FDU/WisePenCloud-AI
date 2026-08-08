# 🧠 WisePen RAG Service

> 让 Agent 像阅读代码一样阅读私有知识。

![Agent Native](https://img.shields.io/badge/Agent--Native-Knowledge%20Navigation-7c3aed?style=for-the-badge)
![Evidence Grounded](https://img.shields.io/badge/Evidence-Source%20Grounded-0ea5e9?style=for-the-badge)
![Permission Aware](https://img.shields.io/badge/Security-Permission%20Aware-16a34a?style=for-the-badge)
![Revision Safe](https://img.shields.io/badge/Consistency-Revision%20Safe-f59e0b?style=for-the-badge)


`wisepen-rag-service` 不是一个“向量库外挂”，也不是把文档切成 chunk 后交给模型猜答案的传统 RAG。它是 WisePen 私有知识体系里的 **Agent-native knowledge substrate**：负责把上游业务资源投影成可检索、可回源、可导航、可权限校验、可跨文档扩展的阅读空间。

代码之所以容易被 Agent 理解，是因为代码天然带有结构：文件、符号、引用、调用链、依赖关系、局部上下文和可逐步展开的入口。私有资料通常没有这套骨架。RAG 服务的价值，就是为企业资料补上这套骨架，让 Agent 不只是“搜到几段文字”，而是能围绕证据继续阅读、验证、跳转和推理。

## ✨ TL;DR

| 传统 RAG | WisePen RAG Service |
| -------- | ------------------- |
| 召回 chunk 后直接拼上下文 | 先定位 Section，再按阅读结构展开 |
| 向量命中近似当证据 | evidence 必须回到原始 Markdown source span |
| 权限常在入口或业务层补过滤 | ACL 下推召回，并贯穿回源与图导航 |
| 更新容易出现半写入窗口 | staged -> applied revision，查询只读完整版本 |
| 图谱常是独立知识层 | 图谱关系必须带证据，并能回到正文继续读 |
| Agent 一次性吃上下文 | locate / sections / cypher 多步导航 |

## 🚀 系统定位

RAG 服务只负责检索需要的派生状态，不拥有业务资源本身，也不直接定义模型可见工具。

```text
Java 业务服务
  -> Kafka 文档 / ACL / 删除事实
  -> wisepen-rag-service
       -> Mongo  : source / resource / projection / derived
       -> Qdrant : 权限感知 hybrid retrieval
       -> Neo4j  : evidence-grounded knowledge graph
       -> Redis  : navigation state
  -> /internal/rag/knowledge-navigation/*
  -> wisepen-mcp-service RAG tools
  -> Agent
```

边界非常明确：

| 边界 | 归属 |
| ---- | ---- |
| 原始资源、生命周期、权威 ACL | 上游业务服务 |
| 内容投影、检索索引、图谱投影、导航状态 | `wisepen-rag-service` |
| Agent tool 名称、描述、模型可见输出 | `wisepen-mcp-service` |
| 最终回答组织与工具调用策略 | Agent / MCP runtime |

这让 RAG 服务保持一个很硬的职责：**把私有知识变成 Agent 可以安全阅读的结构化证据场**。

## 🔥 为什么它不是普通 RAG

普通 RAG 的基本动作是“召回 chunk -> 拼上下文 -> 生成答案”。这个模式在简单问答里能工作，但在企业知识阅读里很容易失控：

- chunk 只是检索单位，不是天然阅读单位。
- 向量命中只能说明相关，不能说明证据完整。
- 模型生成的 offset、引用和上下文边界不能作为事实源。
- 权限如果只在入口检查，很容易在图扩展或回源阶段漏掉。
- 长文档和跨文档问题需要多步阅读，而不是一次性塞满上下文。

WisePen RAG Service 的核心设计是把这些问题拆开处理：

| 问题 | WisePen 的处理方式 |
| ---- | ------------------ |
| 如何定位入口 | 用 dense + BM25 + ranking 找到最相关 Section |
| 如何阅读正文 | 从 Section / ReadingBlock 读取，而不是把 chunk 当正文 |
| 如何保证证据真实 | 所有 evidence 通过 SourceRef 回到原始 Markdown span |
| 如何跨文档扩展 | 用 Neo4j 保存带证据的实体关系和来源关系 |
| 如何控制权限 | ACL 投影下推到 Qdrant，并在回源、图导航阶段继续校验 |
| 如何避免半写入 | staged -> applied revision 发布，查询只读 applied |
| 如何降低 LLM 成本 | contextual indexing 和 graph extraction 派生物按指纹复用 |
| 如何稳定直读资料 | 先拿 document structure，再按 page / section 精确读取 |

所以它不是“检索增强生成”的薄层，而是一个围绕 Agent 阅读行为设计的私有知识导航系统。

## 🧩 核心闭环

### 1. 📚 内容投影：把文档变成阅读骨架

文档完成事件进入后，服务把 Markdown 投影为四类核心结构：

| 产物 | 作用 |
| ---- | ---- |
| `Section` | 标题树节点，保存标题路径、父子关系、正文范围和 preview |
| `ReadingBlock` | Section 内的有序阅读单位，承载 Agent 可读正文 |
| `RetrievalChunk` | 检索单位，用于 embedding、BM25、rerank 和索引 |
| `SourceRef` | 从 chunk / evidence 回到原始 Markdown span 的证据指针 |

关键点是检索和阅读分离。`RetrievalChunk` 负责被找到，`Section` 和 `ReadingBlock` 负责被阅读，`SourceRef` 负责把阅读内容钉回原文。

### 2. ✅ Revision 发布：查询永远只看完整版本

内容写入采用 staged -> applied 模型：

```text
DocumentReady
  -> Section projection
  -> Contextual indexing
  -> Mongo staged revision
  -> Qdrant staged retrieval index
  -> Mongo applied checkpoint
  -> Knowledge graph projection
```

查询侧只读取 applied revision。这样即使内容投影、向量索引、派生文本或图谱抽取发生在不同存储中，Agent 也不会读到半完成状态。

### 3. 🔐 权限感知召回：安全不是事后过滤

RAG 的权限来自上游 ACL 事实，而不是本服务自行推断。ACL 重算事件会刷新本地投影，并同步到 Qdrant 与 Neo4j。

召回阶段先按权限和 applied revision 下推过滤；回源正文和图导航阶段继续校验。权限缺失、状态不匹配或 revision 不一致时，服务选择 fail closed。

### 4. 🎯 Evidence materialization：最终事实必须回源

向量库和知识图谱都不是正文事实源。Qdrant 命中只说明“这里可能相关”，Neo4j 关系只说明“这里可以导航”。真正返回给 Agent 的正文证据，必须通过 `SourceRef` 回到 Mongo 内容投影中的原始 source span。

这条线保证了一个非常重要的系统属性：Agent 可以大胆探索，但最终拿到的是可回溯、可解释、可复核的文本证据。

### 5. 🧭 知识导航：让 Agent 多步阅读

RAG 服务暴露的是知识导航循环，而不是一次性答案接口：

```text
locate
  -> 根据问题找到 Section 入口
  -> 返回 evidence、reading blocks、frontier 和相关图节点

sections
  -> 沿 parent / previous / next / children 读取已发现章节
  -> 扩展标题树 frontier

cypher
  -> 沿已发现图节点做有界关系遍历
  -> 回到 evidence Section 继续阅读
```

这个循环让 Agent 的行为更接近真实研究过程：先定位入口，读局部上下文，再沿章节结构和知识关系继续展开。上下文不是一次性塞进去的，而是在状态约束下逐步获得的。

### 6. 🗂️ Resource Direct Read：让 Agent 稳定读“指定位置”

除了语义定位和图谱导航，RAG 服务还提供一组非常实用的资源直读能力：

| MCP tool | RAG internal API | 价值 |
| -------- | ---------------- | ---- |
| `rag_get_document_structure` | `/internal/rag/resources/document-structure` | 返回当前 applied revision、page labels 和完整 section tree，不携带正文 |
| `rag_get_page_content` | `/internal/rag/resources/page-content` | 按 page label 读取正文，适合“第 5 页”“参考某页”这类明确定位 |
| `rag_get_section_content` | `/internal/rag/resources/section-content` | 按 section id 读取正文，适合“某章节/小节”的稳定阅读 |

这三个工具的价值不在于“智能召回”，而在于**确定性阅读**：当 Agent 已经知道要读哪一页、哪一节，不需要绕一圈向量检索或图谱抽取。它可以先拿结构，再用结构里的 `page_label` / `section_id` 精确读取正文。

这也补上了语义检索天然不擅长的场景：目录浏览、按页核对、按章节精读、引用位置复查、长文档分段阅读。它们和 `locate / sections / cypher` 共同组成两条阅读路径：

```text
Exploratory reading
  -> locate
  -> sections
  -> cypher

Deterministic reading
  -> rag_get_document_structure
  -> rag_get_page_content / rag_get_section_content
```

## 🏗️ 持久化边界

RAG 的存储不是按数据库类型随便堆出来的，而是按职责切分：

| 后端 | 职责 |
| ---- | ---- |
| Mongo source | 原始 Markdown source spans、回源读取能力 |
| Mongo resource | resource snapshot 与读取视图 |
| Mongo projection | Section、ReadingBlock、RetrievalChunk、SourceRef、checkpoint |
| Mongo derived | contextual indexing、graph extraction 等派生文本缓存 |
| Qdrant | dense + sparse hybrid retrieval、ACL payload、revision payload |
| Neo4j projection | 知识图谱节点、关系、MENTIONS 与 evidence 投影 |
| Neo4j navigation | 图谱查询、路径扩展与关系导航 |
| Redis | locate / sections / cypher 之间的短期 navigation state |

这套划分服务于同一个目标：每个仓储都能回答“我是 source、resource、projection、derived、retrieval 还是 navigation”，而不是把不同生命周期和事实来源混在一起。

## 🤖 Agent 可获得什么

通过 MCP 层的 RAG tools，Agent 不只是拿到一段字符串，而是拿到一组可以继续操作的阅读对象：

| 对象 | 价值 |
| ---- | ---- |
| `state_id` | 把多步阅读绑定到同一个会话和权限上下文 |
| `document_structure` | 不带正文的 page label 与 section tree，适合先建立阅读地图 |
| `page_content` | 按页读取的正文窗口，保留 source spans、page labels 和 anchors |
| `section_content` | 按章节读取的正文窗口，不隐式吞入子章节，便于精确控制上下文 |
| `SectionView` | 当前章节正文、标题路径、preview、frontier |
| `evidence` | 可回源的证据文本、page labels、anchors、ref id |
| `frontier` | 下一步可以读取的章节结构 |
| `nodes` / `edges` / `paths` | 可继续探索的知识关系 |

最终效果是：Agent 可以先回答“我看到了什么”，也可以继续问“我应该沿哪个章节、哪个实体、哪条依赖关系读下去”。

## 🧱 核心不变量

- 查询只读取 applied content revision。
- `RetrievalChunk`、`Section`、`ReadingBlock`、`SourceRef` 来自同一个内容投影。
- `RetrievalChunk` 是召回单位，不是最终阅读单位。
- 最终 evidence 必须通过 `SourceRef` 回到原始 Markdown source span。
- Section 树读取是独立导航能力，不依赖图谱抽取。
- 图谱关系必须带原文 evidence，不能替代正文证据。
- document structure 只返回结构，不返回正文；正文必须通过 page / section content 读取。
- page / section content 是确定性直读能力，不依赖语义召回或图谱抽取。
- ACL 在召回、回源、图导航阶段都必须成立。
- 导航状态绑定 session 和用户权限，不允许跨状态读取未发现节点或章节。

## 🛠️ 服务入口

RAG 服务通过 Nacos 注册为：

```text
wisepen-rag-service
```

健康检查：

```text
GET /health
```

内部知识导航接口：

```text
POST /internal/rag/knowledge-navigation/locate
POST /internal/rag/knowledge-navigation/sections
POST /internal/rag/knowledge-navigation/cypher
```

内部资源直读接口：

```text
POST /internal/rag/resources/document-structure
POST /internal/rag/resources/page-content
POST /internal/rag/resources/section-content
```

Kafka 消费入口：

| Topic | 用途 |
| ----- | ---- |
| `wisepen-document-ready-topic` | 文档完成后更新内容投影、检索索引和图谱投影 |
| `wisepen-resource-acl-recalc-topic` | 从上游权威 ACL 重建本地权限投影 |
| `wisepen-resource-physical-destroy-topic` | 清理资源在 RAG 侧的派生状态 |

## ⚡ 本地启动

从 `services/wisepen-rag-service/src/rag` 启动：

```bash
uv run python main.py
```

从 `services/wisepen-rag-service/src` 启动：

```bash
uv run python -m rag.main
```

OpenAPI 文档：

```text
GET /docs
```

## 📖 延伸文档

- [系统设计理念](docs/design.md)
- [核心功能](docs/core_capabilities.md)
- [暴露接口](docs/interfaces.md)
