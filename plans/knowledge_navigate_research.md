# 多跳知识导航调研结论

## 调研基线

| 代码库                                                             | 提交                                         |
|-----------------------------------------------------------------|--------------------------------------------|
| 当前 `formal_pr`                                                  | `490af14eaee01f47b63e6eb10ad30ac229829163` |
| 原 RAG 工作树 `WisePenCloud-AI-new`                                 | `41fc51fb20374e44d45f2cff50aa94503d7ffd62` |
| [PageIndex](https://github.com/VectifyAI/PageIndex)             | `190f8b378be58199ca993566a9214dba72089c54` |
| [SAG-Benchmark](https://github.com/Zleap-AI/SAG-Benchmark)      | `4db43e365aef4dd5b2a87a3111473b0af93b7350` |
| [GitNexus](https://github.com/abhigyanpatwari/GitNexus)         | `bba25b2103f70fef53d0dc16a90e7479ca75046b` |
| [Graphiti](https://github.com/getzep/graphiti)                  | `ca4d5e9d8c5d25d45917427b63daec17603a0d3a` |
| [HippoRAG](https://github.com/OSU-NLP-Group/HippoRAG)           | `1e8f60981bf760b64003aa5bf5668126d0c106b3` |
| [Unstructured](https://github.com/Unstructured-IO/unstructured) | `d309caf8ee20b735eb105d4e16ac3f04e5a48172` |

`formal_pr` 是目标代码基线；原 RAG 工作树只用于确认 Kafka、父子 chunk、Qdrant、ACL、证据物化和现有图增强的真实实现。

## WisePen 现状

### `formal_pr`

- Markdown parser 已按 heading level 维护完整 `TextBlock.section_path`。
- structural chunk 只把 page marker 当硬边界，同页内可能跨 section 聚合。
- parent-child chunker 对重建后的 parent text 再切 child，child 没有唯一 section metadata。
- normalization 的 heading/短尾合并只判断 page，可能重新跨 section 合并。
- 小于 child size 的 parent 不产生 child。
- section locator 表达“本标题到下一个任意标题”，不能直接表示包含 descendants 的 subtree。
- `ToolContentStore` 已保留 section path、page、anchor 和 offset；窗口扩展仍按 chunk 序号。

### 原 RAG 工作树

- `DocumentReadyMessage(resourceId, version, content)` 是正文入口。
- child 是 Qdrant 检索单元，parent 是回答上下文；Mongo 保存两者和 locator。
- Qdrant、ranking、answerability、parent evidence materialization 和本地 ACL predicate 可以复用。
- 当前 request 以单个 `resource_id` 检索，图增强也限制在同一 Resource，无法形成跨文档阅读链。
- `Neo4jWriter(clean_db=True)` 和非 staged/applied 的连续写入不适合多 Resource 高频更新。

因此标题树应进入 RAG 内容 projection，跨文档语义关系另存 Neo4j；两者在 SourceRef 上会合。

## Chunking 社区实践

[Docling HybridChunker](https://docling-project.github.io/docling/concepts/chunking/) 的顺序是先使用文档层级，再按
tokenizer 上限只拆 oversized chunk；undersized peer 也只有 headings/captions 相同时才合并。它的 `contextualize(chunk)` 还把
embedding 输入与原始 `chunk.text` 分开。WisePen 应复用这套设计原则，但 Kafka 已提供 Markdown，不重新接入 chat
`document_parse` 的 `DoclingDocument` 转换。

[Unstructured](https://docs.unstructured.io/concepts/chunking/) 文档同时说明 `by_title` 和 `by_page`。源码中的
`PreChunker` 先调用 boundary predicates 决定语义边界，再由 accumulator 应用 soft/hard max，单个 oversized element
留到第二阶段拆分。WisePen 蒸馏该流程，但只用 enum 直接路由 `AUTO/BY_PAGE/BY_TITLE`，没有引入动态 registry。

[LlamaIndex AutoMergingRetriever](https://docs.llamaindex.ai/en/v0.10.17/examples/retrievers/auto_merging_retriever.html)
索引细粒度 leaf，在多个 leaves 命中同一 parent 时提升为 parent context。这支持把 WisePen 的 retrieval leaf 与
SectionView/PageView 分开，而不是继续维护 arbitrary size parent。

结合 Kafka 正文契约，采用两种输入策略：

- 有可信 `<!-- page N -->`：默认一页一个 retrieval leaf；仅异常超长页在页内按 Section/block fallback。
- 无 page marker：按完整 Section/block 优先装箱；超长 Section 才在内部自然边界切分。

Section 与 Page 始终是独立 source ranges。一页可覆盖多个 Section，一个 Section 也可跨多页；图导航和应用读取通过 spans 物化
Section，不从 chunk 边界反推。

## 通用实体关系社区实践

[Microsoft GraphRAG](https://microsoft.github.io/graphrag/index/default_dataflow/) 的标准知识模型是 `Document/TextUnit/Entity/Relationship/Claim`，默认实体类型可配置，prompt tuning 还能发现未指定类型。关系保留 source、target 和自然语言 description。这说明通用文档抽取的稳定核心应是 Entity、Relationship 和 provenance，而不是某个领域专用实体。

[Neo4j GraphRAG KG Builder](https://neo4j.com/docs/neo4j-graphrag-python/current/user_guide_kg_builder.html) 同时支持手工 schema、自动抽取 schema 和 FREE 模式，并通过 `additional_node_types/additional_relationship_types/additional_patterns` 控制 schema 外输出。WisePen 需要频繁增量更新，因此采用版本化的宽基础 schema：实体统一为 `KnowledgeEntity + entity_type`，关系由 core、learning、scholarly profiles 组合；不按每篇文档动态生成 ontology。

[Graphiti custom entity and edge types](https://help.getzep.com/graphiti/core-concepts/custom-entity-and-edge-types/) 使用 Pydantic 扩展领域类型，并为未覆盖的实体对保留 `RELATES_TO` fallback。可迁移的点是少量通用类型、明确 endpoint mapping 和 fallback predicate；不迁移它的 episode/temporal runtime。

[Schema.org CreativeWork](https://schema.org/CreativeWork) 的 `citation/mentions/about/isPartOf/isBasedOn` 与 [W3C PROV-O](https://www.w3.org/TR/prov-o/) 的 `wasDerivedFrom/wasAttributedTo/wasRevisionOf/hadPrimarySource` 提供了通用文档与来源关系词汇。WisePen 的 core relation 以这些语义为参照，再增加产品、技术、方法等常见关系。

论文引用采用普通图对象。 [Crossref](https://www.crossref.org/documentation/principles-practices/best-practices/relationships/) 用标识符和受控关系连接论文、数据、软件、作者和组织；[Semantic Scholar](https://api.semanticscholar.org/api-docs/graphs) 的 reference/citation API 还保留 citation context 和 intent。WisePen 因此先解析 bibliography、DOI、URL 和标题，再由 LLM 从正文补引用目的，统一写入 `KnowledgeEntity` 和 scholarly relations。

最终实体类型覆盖 Concept、Person、Organization、Product、Technology、Method、Dataset、Event、Place、Document 和 Other。关系分为默认 core，以及 learning、scholarly 两个可选 profile；三者复用同一 EntityMention、EntityResolver、SourceRef 和 Neo4j 查询契约。

## PageIndex

源码入口：`pageindex/page_index_md.py`、`pageindex/page_index.py`、`pageindex/retrieve.py`。

Markdown 路径按 `#` level 用栈构树，节点包含 title、node ID、行号、正文和 children；父节点 token count 会聚合所有
descendants。PDF 无可靠目录时，代码分组读取带物理页标记的正文，让 LLM 逐段生成/续写目录，再校验页码单调性、标题位置和相邻已验证边界；过大的节点继续递归切分。

可迁移设计：

- 每个结构节点同时有直接正文范围和包含 descendants 的 subtree range。
- Agent 先看不带全文的树，再按稳定位置打开正文。
- 远端命中先绑定树节点，再读取祖先、同级前置和 children，而不是只返回相邻字符。
- 对弱结构文档，生成的标题与范围必须回到原文位置验证。

WisePen 的 Markdown heading 已可确定解析，MVP 直接建树，不需要 PageIndex 的 LLM 目录生成。弱结构修复只作为后续实验，并沿用它的
offset、顺序和邻接边界校验。

## SAG-Benchmark

源码入口：`prompts/extract.yaml`、`pipeline/modules/extract/processor.py`、`extractor.py`、`parser.py`、
`pipeline/modules/search/step5_strategies.py`。

其抽取输入包含文档标题、当前 items、previous context、related events 和带描述的受控实体类型；输出中的 `references` 是
1-based 原文 item 索引。解析器把引用映射回真实 section ID，实体在当前 event 中的作用写在关联描述上。多跳检索维护已访问
entity/event 集合，HopLLM 先粗排一跳结果，再用精选实体作为下一阶段种子。

可迁移设计：

- 实体类型由 schema 和描述约束，角色说明属于当前 mention/关系，不污染 canonical Entity。
- 每个 mention 和 relation 都绑定输入片段的真实 ID 与原文 span。
- 共享 canonical Entity 用于发现跨 chunk、跨文档候选。
- 每跳先排序种子，并维护 visited set 和候选预算。

SAG 的强制 Event 中心模型不适合通用文档；`name.lower()` 的 exact 去重不足以解决同义词和同名异义；引用为空时回退到全部
items 会伪造证据。WisePen MVP 改用 `EntityMention -> KnowledgeEntity`，引用无效时丢弃 candidate。

## GitNexus

源码入口：`gitnexus/src/mcp/tools.ts`、`local/local-backend.ts`、`output-budget.ts`、`core/search/hybrid-search.ts`。

它把 Agent 导航拆成稳定步骤：`query` 找入口，`context` 返回一个稳定 UID 的 incoming/outgoing 和流程归属，`impact` 按
direction、relation type 和 depth 展开。重名符号返回 ranked candidates，不静默选择；大结果按 depth 分页，并显式返回
`partial`、`truncated` 和总数。BM25 与 semantic 用 RRF 合并，但导航层本身不重新实现源码读取。

迁移到 WisePen：

- `locate` 返回稳定 Entity/section/source ID；歧义 Entity 返回候选及证据。
- `expand` 固定 relation、direction、depth 1 至 2、visited set 和预算。
- 每个远端节点附 SourceRef，并通过原内容读取设施打开证据。
- 返回 `partial/truncated/exhausted`，避免 Agent 把被裁剪结果理解为完整图。

GitNexus 的 AST、调用图和 process/community 模型不进入本文档图。

## 补充项目

| 项目                    | 值得参考                                                               | 结论                                                       |
|-----------------------|--------------------------------------------------------------------|----------------------------------------------------------|
| Graphiti              | episode provenance、custom Pydantic types、增量 entity/edge resolution | 完整框架包含时间事实、摘要、检索和多轮 LLM 调用；只参考证据和增量边界                    |
| HippoRAG 2            | query entities 作为图种子、PPR/多跳候选排序                                    | 偏完整 OpenIE + PPR 检索流水线；MVP 先验证有界 traversal 和现有 ranking   |
| Neo4j GraphRAG Python | `LLMEntityRelationExtractor`、`GraphSchema`、structured output       | 作为候选抽取组件，隔离在 adapter 后；官方 KG Builder API 仍为 experimental |

## 技术选择

| 问题       | 选择                                                                             |
|----------|--------------------------------------------------------------------------------|
| 底层 chunk | source-backed blocks；paginated 一页一 leaf，flowing 按 Section/block hybrid packing |
| 文档内结构    | 从现有 blocks 构建 Mongo SectionTree，不写 Neo4j                                       |
| 初始定位     | 复用 Qdrant hybrid 和 `RankingPipeline`                                           |
| 跨文档桥     | `EntityMention -> KnowledgeEntity` 与有证据的 core/learning/scholarly relation      |
| LLM 抽取   | `neo4j-graphrag` 的 `LLMEntityRelationExtractor`，固定 schema + structured output  |
| 实体解析     | stable ID/exact alias -> 同类型候选召回 -> evidence 判定；歧义时才调用 LLM                   |
| 图存储/检索   | Neo4j，一至两跳固定 Cypher pattern                                                    |
| 原文回读     | Mongo SourceRef + `ToolContentStore`                                           |
| ACL      | Java Kafka -> 本地 Mongo/Qdrant/Neo4j projection                                 |
| 更新       | Resource version staged/applied；按 changed section/chunk 重算                     |

没有选择任何项目的完整 GraphRAG 路线。目标是把标题树、canonical Entity 和现有 RAG 组合成可验证的导航链路。
