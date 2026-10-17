# 中立数据模型

本文定义整套文档使用的事实、身份和投影术语。内容与图模型位于 [core/schema](../src/rag/core/schema/__init__.py)，是普通 dataclass；它们不拥有发布、权限或后端连接。应用结果见各[用例说明](README.md)。

## 1. 内容事实与身份

源码：[content.py](../src/rag/core/schema/content.py)。

| 模型 | 字段 | 语义 |
|---|---|---|
| `ContentRevision` | resource_id、document_version、content_sha256 | frozen/slots；资源、上游版本与完整原文摘要共同确定不可变身份 |
| `Document` | resource_id、document_version、content_revision、text、structure、metadata、type | 一次 revision 的完整文档；text 是原始 Markdown；metadata 默认 `{}`，type 默认 None |
| `DocumentStructure` | total_length、sections、anchors | total_length 是 text 的 Python 字符数；结构复用 Common Section/Anchor，列表默认空 |
| `Chunk` | chunk_id、resource_id、content_revision、chunk_index、section_id、section_path、raw_text、content_token_count、anchor_labels、metadata | revision 内的分块事实；正文、顺序和结构归属由分块阶段确定 |

`ContentRevision.create(resource_id=..., document_version=..., text=...)` 对原始 text 的 UTF-8 字节计算完整 SHA-256，不 trim、不规范化 Unicode。其 `content_revision` 属性为：

```text
resource_id@document_version#完整 SHA-256
```

type、metadata、本体、分块和 embedding 配置都不参与摘要。已发布的同身份提交会跳过；影响摄入的 type/metadata 变化必须提高上游 version。构建配置变化不会自动重建，见[发布](usecases-publication.md)和[扩展](extensions.md)。

`Document.type` 是开放字符串路由键，None 走默认 Pipeline，与资源物理类型、本体 domain 是不同概念。Chunk 没有第二个 type 字段。

### 章节与原文范围

Document 的 sections 是 Common `Section`，anchors 是 Common `Anchor`，定义见 [Common chunker.py](../../wisepen-common/src/common/utils/markdown/chunking/chunker.py)。own_span/subtree_span 使用 Python 字符偏移和半开区间，直接切 `Document.text[start:end]`；不是 UTF-8 字节或 token 偏移。标题、空白及 Markdown 均属于原文。

Document 的章节和父章节 ID、Chunk.section_id、检索结果 section_id 使用同一 `rag_section_id`：

```text
rsec_ + sha256(resource_id + "\0" + content_revision + "\0" + common_section_id)[:24]
```

调用方直接传回 ID，不解析、不另行拼接；旧 revision 的 ID 不会切换到新版。Chunk.chunk_id 由 [MarkdownChunker](../src/rag/ingestion/chunkers/markdown.py) 生成为 `resource_id:content_revision:common_chunk_id`，不因章节 ID 转换而改写。

### 正文与检索文本

Chunk.raw_text 是分块正文事实。`content_token_count` 只统计正文，不包含生成上下文。`retrieval_text` 是计算属性，用空行连接非空的 `">".join(section_path)`、去掉首尾空白的 metadata.contextual_context 和 raw_text；不持久化为第二份正文。

索引使用 retrieval_text，正文命中和普通重排使用 raw_text，章节读取切 Document.text。生成上下文及图事实放在普通 metadata 中，增量与覆盖语义见[核心抽象](abstractions.md)。

## 2. 图事实、本体与来源

源码：[graph.py](../src/rag/core/schema/graph.py)。

| 模型 | 字段 | 语义 |
|---|---|---|
| `GraphNode` | name、source_id、ontology_name、category、description、relations | 单个 Chunk 的实体事实；source_id 是来源 **Chunk ID**；可选字段默认 None，relations 默认空 |
| `GraphEdge` | source_id、target_id、relation_type、description、keywords、source_ids、metadata | 有向关系；source_id/target_id 是 **GraphNode.id**；source_ids 才是来源 Chunk 列表 |
| `GraphRelationOption` | relation_type、direction | 类别允许的关系提示，不承诺存在可见关系 |
| `EntitySpec` | category、description | 本体实体类别说明 |
| `RelationSpec` | relation_type、description、allowed_sources、allowed_targets | 有向谓词和端点类别；某侧列表为空表示不限制该侧 |
| `Ontology` | domain、description、entity_specs、relation_specs | 写入侧绑定的本体；domain 映射 ontology_name；说明默认空字符串，两种 specs 默认空字典 |

`GraphDirection` 为 outgoing/incoming/both。Ontology 可直接构造，也可通过 `Ontology.from_file(path)` 读取 JSON/YAML/YML（支持 UTF-8 BOM），文件边界解析并校验声明。

### 逻辑身份与来源身份

GraphNode.id 把 name 做 strip、casefold、合并空白，category 和 ontology_name 保持原值。非空本体下为 `ontology_name:category:normalized_name`，无本体时为 `category:normalized_name`，缺失类别保留空槽。描述与来源不参与逻辑身份，不做别名解析或实体链接。

GraphEdge.id 为 `source_id::relation_type::target_id`，方向影响身份。相同逻辑实体/关系可以由多个 Chunk 支持；每个来源描述独立保存，不聚合成一条权威描述。Node/Edge 本身没有 resource_id、content_revision 或 ACL，资源身份沿来源 Chunk 回查。

### 写入与读取的校验边界

`Ontology.create_node/create_edge` 构造绑定来源的事实；`Ontology.parse_chunk_facts` 在通用结构解析之上增加本体约束，strict=True 用于成功缓存完整校验。`parse_graph_facts(chunk)` 只核对结构、来源和合法端点，供不绑定本体的读取用例使用。

图事实以 dataclass 转字典后存入 Chunk.metadata.entities/relationships；读回仍是普通字典，由边界解析。成功缓存使用 graph_version；两个事实列表均缺失表示无图事实，损坏结构不能当成空图。图查询证据与正文种子的具体降级差异见[图用例](usecases-graph.md)和[向量用例](usecases-vector.md)。

## 3. 通用 metadata 条件

源码：[filters.py](../src/rag/core/schema/filters.py)。

| 模型 | 字段/取值 | 语义 |
|---|---|---|
| `MetadataFilter` | key、value、operator=EQ | 单字段条件；key 是 metadata 内相对字段名；value 支持标量及字符串/整数列表 |
| `FilterOperator` | eq、in、gt、gte、lt、lte | 模型表达能力；具体 adapter/用例可支持其中子集 |
| `MetadataFilters` | filters、condition=AND | 可递归嵌套的条件组；后端要求非空，NOT 恰好一个子条件 |
| `FilterCondition` | and、or、not | 条件组合 |

模型不解释 ACL、发布状态或业务含义。Qdrant 使用平面字段；图用例仅允许三个字符串字段及 EQ/IN，详见[图筛选合同](usecases-graph.md)。不要根据枚举存在 GT/GTE 推断图查询支持年份范围。

## 4. storage 值对象：投影合同

这些对象中立于数据库 SDK，但属于存储端口，不是第二套内容事实模型。

| 对象 | 字段与用途 |
|---|---|
| `VectorRecord` | record_id、text、embedding、metadata；text 用于 sparse 构建，后端不要求保存正文 |
| `VectorQuery` | embedding 或 text 二选一、top_k=10、filters=None、include_embedding=False；一次单路召回 |
| `VectorMatch` | record_id、score、metadata、embedding=None；定位及 provider 分数，不含正文或授权决定 |
| `IndexKind` | KEYWORD/INTEGER/FLOAT/BOOLEAN；无 DATETIME，时间可由业务转整数时间戳 |
| `IndexableField` | name、kind；metadata 平面字段的后端无关索引声明 |
| `VectorCollection` | storage、metadata_index_fields；装配层初始化配置，GC 只消费 storage |
| `GraphSourceProjection` | chunk_id、metadata、nodes=[]、edges=[]；一个来源的完整图投影，空事实也会移除该来源旧贡献 |
| `GraphVectorKind` | entity/relationship；区分两种图向量 |

定义见 [storage/vector.py](../src/rag/storage/vector.py) 和 [storage/graph.py](../src/rag/storage/graph.py)。图向量记录 ID 由 kind/来源 Chunk/逻辑 fact ID 共同生成，不能与 GraphNode.id 混用；fact_sha256 核验当前事实输入，text_sha256 记录索引文本摘要。

## 5. 应用层拥有的对象

ResourcePublicationState/PublicationAction 属于[发布用例](usecases-publication.md)；ResourceAcl/PermissionScope 和 ResourceSnapshot 属于[授权用例](usecases-access.md)。它们不嵌入 Document/Chunk。

ChunkHit、GraphRetrievalResult/GraphTraversalResult、DocumentOutline/SectionOutline/SectionText 和 GcReport 是各用例结果，不是数据库镜像模型。图结果的 GraphEntity 和 GraphRelationship 都只包含 id 和 sources，每个逻辑对象只出现一次。实体来源为 chunk_id/description，关系来源额外保留各自的 keywords/metadata；不拼接描述、不生成摘要、不合并不同来源的关键词。核心模型及持久化不变，应用实体不再暴露名称、本体、类别或本体允许提示，应用关系不再重复返回端点与谓词。内部运算使用核心模型的结构化字段，不解析返回 ID。应用响应中的 score 表示最终排序阶段，不能一概解释成相似度或重排分数。
