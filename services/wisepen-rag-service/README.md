# WisePen RAG Service

让 AI 像阅读代码一样阅读私有资料。

Codex 这类 Agent 读代码很高效，不只是因为代码被索引了，而是因为代码天然有清晰的结构：文件、符号、引用、调用链、依赖关系，以及可以逐步展开的上下文。

私有资料通常没有这么好的“可读性骨架”。一份文档可能很长，章节关系松散，引用隐含在自然语言里，相关知识分散在不同资源中。WisePen RAG Service 的目标，就是给这些资料补上一套适合 Agent 阅读的结构：章节队列、证据回源、权限感知召回和跨文档图扩展。

## 为什么存在

传统 RAG 容易停留在“召回几个 chunk，然后让模型自己猜上下文”。这对问答够用，但不适合复杂资料阅读。真正好用的 Agent 需要像读代码那样工作：

- **先定位入口**：从问题找到最值得读的 Section，而不是把一堆碎片直接塞进上下文。
- **再按结构阅读**：沿着 parent、previous、next、children 扩展章节队列，像在代码库里打开相邻文件和调用点。
- **按结构读取**：直接按 page / section 读正文，专门覆盖“见某某章节”“见某某页”这类稳定定位问题，这类需求图抽取往往做不到稳定低成本覆盖。
- **最后顺关系跳转**：通过实体、依赖、引用、来源和解释关系，在不同文档之间继续探索。

所以这个服务不是一个“把文档切块塞进向量库”的薄封装，而是一套面向 Agent 阅读过程设计的私有知识导航系统。

## 系统骨架

```text
Kafka 文档 / ACL / 删除事件
        |
        v
WisePen RAG Service
  -> Mongo  : 内容投影、ACL 投影、派生文本缓存
  -> Qdrant : 权限感知混合召回索引
  -> Neo4j  : 跨文档知识图谱
  -> Redis  : 短期导航状态
        |
        v
/internal/rag/knowledge-navigation/*
        |
        v
wisepen-mcp-service RAG tools
```

服务通过 Nacos 注册为 `wisepen-rag-service`。Agent tool 的定义与模型可见契约不在本服务中，统一由 `wisepen-mcp-service` 暴露。

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 内容投影 | 把 Markdown 投影成 Section、ReadingBlock、RetrievalChunk 和 SourceRef，形成 Agent 可遍历的阅读骨架 |
| 章节队列 | 通过 Section frontier 暴露 parent、previous、next、children，让 Agent 能按文档结构逐步读 |
| 权限感知召回 | 在 Qdrant 中执行 dense + BM25 混合召回，同时携带 ACL 与 applied revision 过滤 |
| 证据回源 | 所有最终正文都从 Mongo 内容投影按 SourceRef 精确回读，不信任模型生成的 offset |
| 图扩展 | 用 Neo4j 保存概念、依赖、引用和来源关系，让 Agent 能像追调用链一样追知识链 |
| 派生物复用 | Contextual indexing 和 GraphRAG 抽取结果按输入指纹持久化，减少重复 LLM 调用 |

## Agent 阅读循环

```text
locate(query)
  -> 找到最相关的 Section 入口
  -> 返回正文证据、章节 frontier 和已命中的图节点

sections(state_id, section_ids)
  -> 读取已发现 Section 的完整正文
  -> 扩展下一批可读章节

cypher(state_id, node_ids)
  -> 沿知识图谱查询相关概念和关系
  -> 回到证据 Section 继续阅读
```

这个循环让 Agent 不必一次性吞下整份资料，而是像读代码一样：先定位，读局部上下文，再沿结构和依赖继续展开。

## 本地启动

从 `services/wisepen-rag-service/src/rag` 启动：

```bash
uv run python main.py
```

从 `services/wisepen-rag-service/src` 启动：

```bash
uv run python -m rag.main
```

健康检查：

```text
GET /health
```

## 文档

- [系统设计理念](docs/design.md)
- [核心功能](docs/core_capabilities.md)
- [暴露接口](docs/interfaces.md)
