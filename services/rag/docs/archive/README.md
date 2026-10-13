# 历史材料归档

本目录保存原始计划、设计、接手、实施记录及 demo/扩展文稿。**这些资料可能与现状不符；现行合同从 [docs 入口](../README.md)开始阅读。**

2026-10-13 整合时，42 个原文件按字节原样移动，移动前后 SHA-256 一致；未改写正文、历史测试数字或相对链接。plans/demo 已不再维护，Employment 原 README 为避免与此入口同名，归档为 employment-README.md。扩展源码、本体、业务 JSON/JSONL 保持原位。

## 阅读方式

- 00–11 是问题、方案和阶段计划，架构图也是历史图，不能据此判断当前类/文件存在。
- 12–15、17–31 是实施记录，16 是当时接手快照；测试数字仅解释当时验证范围。
- 29 的向量协议、30 的本体条件和 31 的内部合同/metadata 修正可按顺序阅读；当前双默认 Pipeline 以现行扩展说明和源码为准。
- 新说明见[设计](../design.md)、[关键决策](../decisions.md)和[项目回顾](../retrospective.md)。

历史文本保留旧相对路径，所以某些链接移动后失效；本目录内同名计划链接通常仍可用。寻找旧源码链接时按下表还原原文位置，再定位当前 [Container](../../src/rag/container.py)、[上下文 repair](../../scripts/repair_context.py)、[图 repair](../../scripts/repair_graph.py)或[扩展源码](../../src/rag/extensions/__init__.py)。不要把失效链接理解成当前组件待实现，也不要批量修订原稿。

## 原路径对照

原路径以 services/rag/ 为基准；下列归档链接都指向本目录。

| 原路径 | 归档文件 |
|---|---|
| `demo/contextual.md` | [contextual.md](contextual.md) |
| `demo/graph-ontology.json` | [graph-ontology.json](graph-ontology.json) |
| `demo/graph-ontology.yaml` | [graph-ontology.yaml](graph-ontology.yaml) |
| `demo/graph.md` | [graph.md](graph.md) |
| `demo/hybrid.md` | [hybrid.md](hybrid.md) |
| `demo/platform-settings.yaml` | [platform-settings.yaml](platform-settings.yaml) |
| `demo/platform.md` | [platform.md](platform.md) |
| `plans/00-rag-v4-restructure-plan.md` | [00-rag-v4-restructure-plan.md](00-rag-v4-restructure-plan.md) |
| `plans/01-问题与根因.md` | [01-问题与根因.md](01-%E9%97%AE%E9%A2%98%E4%B8%8E%E6%A0%B9%E5%9B%A0.md) |
| `plans/02-可信身份与 ACL 边界.md` | [02-可信身份与 ACL 边界.md](02-%E5%8F%AF%E4%BF%A1%E8%BA%AB%E4%BB%BD%E4%B8%8E%20ACL%20%E8%BE%B9%E7%95%8C.md) |
| `plans/03-ACL 基础投影与一致性边界.md` | [03-ACL 基础投影与一致性边界.md](03-ACL%20%E5%9F%BA%E7%A1%80%E6%8A%95%E5%BD%B1%E4%B8%8E%E4%B8%80%E8%87%B4%E6%80%A7%E8%BE%B9%E7%95%8C.md) |
| `plans/04-内容版本与请求内读取边界.md` | [04-内容版本与请求内读取边界.md](04-%E5%86%85%E5%AE%B9%E7%89%88%E6%9C%AC%E4%B8%8E%E8%AF%B7%E6%B1%82%E5%86%85%E8%AF%BB%E5%8F%96%E8%BE%B9%E7%95%8C.md) |
| `plans/05-Chunk 与 Document 级图谱边界.md` | [05-Chunk 与 Document 级图谱边界.md](05-Chunk%20%E4%B8%8E%20Document%20%E7%BA%A7%E5%9B%BE%E8%B0%B1%E8%BE%B9%E7%95%8C.md) |
| `plans/06-Chunk 模型与 LightRAG 图谱查询合同.md` | [06-Chunk 模型与 LightRAG 图谱查询合同.md](06-Chunk%20%E6%A8%A1%E5%9E%8B%E4%B8%8E%20LightRAG%20%E5%9B%BE%E8%B0%B1%E6%9F%A5%E8%AF%A2%E5%90%88%E5%90%8C.md) |
| `plans/07-摄入管线与五类核心抽象.md` | [07-摄入管线与五类核心抽象.md](07-%E6%91%84%E5%85%A5%E7%AE%A1%E7%BA%BF%E4%B8%8E%E4%BA%94%E7%B1%BB%E6%A0%B8%E5%BF%83%E6%8A%BD%E8%B1%A1.md) |
| `plans/08-Ontology 约束与 LightRAG 图谱装配.md` | [08-Ontology 约束与 LightRAG 图谱装配.md](08-Ontology%20%E7%BA%A6%E6%9D%9F%E4%B8%8E%20LightRAG%20%E5%9B%BE%E8%B0%B1%E8%A3%85%E9%85%8D.md) |
| `plans/09-VectorStorage 与 GraphStorage 最小存储合同.md` | [09-VectorStorage 与 GraphStorage 最小存储合同.md](09-VectorStorage%20%E4%B8%8E%20GraphStorage%20%E6%9C%80%E5%B0%8F%E5%AD%98%E5%82%A8%E5%90%88%E5%90%8C.md) |
| `plans/10-实施排期与验收门槛.md` | [10-实施排期与验收门槛.md](10-%E5%AE%9E%E6%96%BD%E6%8E%92%E6%9C%9F%E4%B8%8E%E9%AA%8C%E6%94%B6%E9%97%A8%E6%A7%9B.md) |
| `plans/11-RAG-V4设计文稿.md` | [11-RAG-V4设计文稿.md](11-RAG-V4%E8%AE%BE%E8%AE%A1%E6%96%87%E7%A8%BF.md) |
| `plans/12-schema-ingestion-implementation.md` | [12-schema-ingestion-implementation.md](12-schema-ingestion-implementation.md) |
| `plans/13-content-storage-implementation.md` | [13-content-storage-implementation.md](13-content-storage-implementation.md) |
| `plans/14-publication-implementation.md` | [14-publication-implementation.md](14-publication-implementation.md) |
| `plans/15-kafka-publication-implementation.md` | [15-kafka-publication-implementation.md](15-kafka-publication-implementation.md) |
| `plans/16-session-handoff.md` | [16-session-handoff.md](16-session-handoff.md) |
| `plans/17-vector-publication-implementation.md` | [17-vector-publication-implementation.md](17-vector-publication-implementation.md) |
| `plans/18-application-snapshot-implementation.md` | [18-application-snapshot-implementation.md](18-application-snapshot-implementation.md) |
| `plans/19-hybrid-retrieval-implementation.md` | [19-hybrid-retrieval-implementation.md](19-hybrid-retrieval-implementation.md) |
| `plans/20-structured-reading-implementation.md` | [20-structured-reading-implementation.md](20-structured-reading-implementation.md) |
| `plans/21-contextualizer-implementation.md` | [21-contextualizer-implementation.md](21-contextualizer-implementation.md) |
| `plans/22-graph-extraction-implementation.md` | [22-graph-extraction-implementation.md](22-graph-extraction-implementation.md) |
| `plans/23-graph-projection-implementation.md` | [23-graph-projection-implementation.md](23-graph-projection-implementation.md) |
| `plans/24-graph-vector-retriever-implementation.md` | [24-graph-vector-retriever-implementation.md](24-graph-vector-retriever-implementation.md) |
| `plans/25-graph-application-implementation.md` | [25-graph-application-implementation.md](25-graph-application-implementation.md) |
| `plans/26-platform-gc-implementation.md` | [26-platform-gc-implementation.md](26-platform-gc-implementation.md) |
| `plans/27-document-type-routing-implementation.md` | [27-document-type-routing-implementation.md](27-document-type-routing-implementation.md) |
| `plans/28-employment-extension-implementation.md` | [28-employment-extension-implementation.md](28-employment-extension-implementation.md) |
| `plans/29-vector-index-contract-implementation.md` | [29-vector-index-contract-implementation.md](29-vector-index-contract-implementation.md) |
| `plans/30-graph-ontology-filter-implementation.md` | [30-graph-ontology-filter-implementation.md](30-graph-ontology-filter-implementation.md) |
| `plans/31-employment-department-metadata-implementation.md` | [31-employment-department-metadata-implementation.md](31-employment-department-metadata-implementation.md) |
| `plans/架构图.md` | [架构图.md](%E6%9E%B6%E6%9E%84%E5%9B%BE.md) |
| `src/rag/extensions/employment/README.md` | [employment-README.md](employment-README.md) |
| `src/rag/extensions/extensions.md` | [extensions.md](extensions.md) |
