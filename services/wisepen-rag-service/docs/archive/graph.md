# Ontology 图事实抽取与离线 repair

当前支持 Chunk 图事实及可选 Neo4j 来源拓扑投影。平台入口已装配默认发布/查询与 GC，配置见 [platform.md](platform.md)。默认
Pipeline 不自动生成图事实；扩展包须显式传 Ontology，流程见 [extensions.md](../src/rag/extensions/extensions.md)。

## 显式装配

调用方读取已有 Document 和完整 Chunk 列表，并保证同资源写入串行。无需重新分块：

```python
from common.utils.ai_clients import InstructionClient
from rag.core.schema import Ontology
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.transformers import GraphExtractor

ontology = Ontology.from_file("services/rag/demo/graph-ontology.json")
extractor = GraphExtractor(
    ontology=ontology,
    client=InstructionClient(openai_client, model=instruction_model),
    gleaning=False,
)
chunks = await extractor.transform(chunks, document=document)
await ChunkFactsIndexer(content_storage).index(chunks)
```

客户端、模型、存储、Document 和 chunks 均由调用方显式提供。GraphExtractor 不选择 applied、不判 ACL、不写存储；现有
IngestionPipeline 也可把它作为 Transformer 使用。普通 publication 的必需索引门槛不增加图谱。

Ontology、EntitySpec、RelationSpec、GraphNode 和 GraphEdge 都是 core.schema.graph 中的标准 dataclass；只有传给 Instructor
的输出 DTO 是 Pydantic。from_file 支持 .json、.yaml、.yml 和 UTF-8 BOM，使用 safe_load 解析
YAML，再在文件边界校验字段类型、未知字段、声明键值与端点类别；内部可直接构造：

```python
from rag.core.schema import EntitySpec, Ontology, RelationSpec

ontology = Ontology(
    domain="work-demo",
    entity_specs={
        "Person": EntitySpec("Person", "人员"),
        "Organization": EntitySpec("Organization", "组织"),
    },
    relation_specs={
        "works_at": RelationSpec(
            "works_at", "人员在组织任职",
            allowed_sources=["Person"], allowed_targets=["Organization"],
        ),
    },
)
```

配置文件可自行集中放入 ontologies/，from_file 不扫描目录或建立 Registry。

可直接对比 [JSON 示例](graph-ontology.json) 和 [YAML 示例](graph-ontology.yaml)：两份配置表达相同本体，允许 Person →
works_at → Organization，YAML 版本附有字段说明注释。加载时只需替换文件路径：

```python
ontology = Ontology.from_file("services/rag/demo/graph-ontology.yaml")
```

材料只有当前 raw_text 和 section_path。标题只帮助消歧，不读取生成上下文、邻居或全文。节点来源由程序绑定当前 Chunk ID；关系
source_id/target_id 是节点 ID，source_ids 才是来源 Chunk 列表。

成功时替换 entities、relationships、graph_version，保留其他 metadata。内容存储读回事实列表仍为普通字典。有效空结果也缓存；指纹过期或缓存损坏/来源不符时重算整块。每实例绑定一个
Ontology，不在同一 metadata 中累积多个本体。

## 补漏与失败

默认关闭补漏；gleaning=True 最多追加一轮，复用当前块首次对话。提示词只允许增量事实，无增量返回空列表。首轮合法 ID
的字段始终保留，新关系可以引用首轮实体。不合法类别、谓词和端点逐条丢弃，日志记录拒绝原因。

默认输入预算 8000 token、每轮输出上限 4096 token、共享实例模型并发上限 4。预算包含渲染消息和结构化 schema 文本，不等于供应商聊天封装的精确
token 数。首轮超预算报错且不截断原文；补漏历史超预算记录日志并保留首轮结果。Instructor 自身纠错重试可能增加 HTTP 次数，不是补漏。

模型/整份响应异常、重试耗尽和取消传播；其他任务取消后等待收尾。所有块成功后才修改图
metadata，调用方随后保存。图失败不撤销普通发布；写入失败不回滚部分成功。

## 离线 repair

维护窗口内禁止同资源其他 Chunk 写任务。通过 CLI 或专用环境变量提供连接配置：

```text
RAG_GRAPH_REPAIR_MONGO_URL
RAG_GRAPH_REPAIR_MONGO_DATABASE
RAG_GRAPH_REPAIR_INSTRUCTION_BASE_URL
RAG_GRAPH_REPAIR_INSTRUCTION_API_KEY
RAG_GRAPH_REPAIR_INSTRUCTION_MODEL
```

从仓库根目录执行：

```powershell
.venv/Scripts/python.exe services/rag/scripts/repair_graph.py `
    --ontology-file services/rag/demo/graph-ontology.json `
    --resource-id RESOURCE_ID
```

资源可重复传 --resource-id，或用 --resource-file 指定每行一个 ID 的 UTF-8 文件，去重保序。连接配置也可用
--mongo-url、--mongo-database、--instruction-base-url、--instruction-api-key、--instruction-model 显式传入。

--gleaning 开启补漏；--force 禁用增量复用。预算和并发使用 --max-input-tokens、--max-output-tokens、--max-concurrency。

repair 选择当前 applied 未 destroyed 的资源，核对完整正文身份并读取既有块；写前复核完整发布状态，变化则跳过。成功后保存全部
Chunk，即使全由缓存复用。资源失败继续下一资源，输出 repaired/skipped/document_errors；有错误退出 1，否则退出 0。setup 失败退出
1，取消传播，全部客户端关闭。

脚本不加载 Nacos、不 stage/apply、不启动消费者、不重分块、不重写普通向量。默认只修复事实，状态复核遵循维护窗口约定，不提供并发更新协议。

## 可选 Neo4j 投影

配置以下变量，或使用同名 CLI 参数（如 --neo4j-uri、--neo4j-username、--neo4j-password）：

```text
RAG_GRAPH_REPAIR_NEO4J_URI
RAG_GRAPH_REPAIR_NEO4J_USERNAME
RAG_GRAPH_REPAIR_NEO4J_PASSWORD
RAG_GRAPH_REPAIR_NEO4J_DATABASE       # 默认 neo4j
RAG_GRAPH_REPAIR_NEO4J_GRAPH_NAME     # 默认 rag_v4
```

```powershell
.venv/Scripts/python.exe services/rag/scripts/repair_graph.py `
    --ontology-file services/rag/demo/graph-ontology.yaml `
    --resource-id RESOURCE_ID `
    --with-graph
```

固定顺序是抽取/缓存复用 → 写前状态复核 → 保存全部 Chunk → GraphIndexer。即使全部命中抽取缓存，也重写图投影；Mongo 成功但
Neo4j 失败时保留事实，资源计错并非零退出，重跑可以复用模型结果。不开 --with-graph 时不创建 Neo4j driver。

也可以单独从既有事实投影，不调用模型：

```python
from neo4j import AsyncGraphDatabase
from rag.backends.neo4j import Neo4jGraphStorage
from rag.ingestion.indexers import GraphIndexer

async with AsyncGraphDatabase.driver(neo4j_uri, auth=neo4j_auth) as driver:
    topology = Neo4jGraphStorage(driver=driver)
    await topology.ensure_schema()
    await GraphIndexer(ontology=ontology, writer=topology).index(stored_chunks)
```

driver、URI/auth、Ontology 和已读回的完整 Chunk 列表由调用方提供；同资源写任务串行。当前默认每批 64
个来源，用显式事务完整替换该批来源，空事实移除旧贡献，其他来源保留。写入阶段失败回滚当前批，之前成功批次保留；提交确认网络异常可能结果未知，不报告成功，重新替换同一来源是幂等的。

共享实体/有向关系只保存逻辑身份，每个来源关联分别保存名称、描述、关键词和关系 metadata。读回同一 ID
可以得到多个来源事实，不生成聚合描述。graph_name 绑定独立标签/约束，不进入 GraphNode/GraphEdge ID；ensure_schema 显式执行，CRUD
不隐式建索引。

GraphReader 支持 get_nodes、get_relations、get_neighbors。一跳方向为 outgoing/incoming/both，谓词可过滤，默认 limit=100
限制逻辑关系数，选中关系的匹配来源完整返回。MetadataFilters 只执行来源 metadata 的平面属性条件，不代表 ACL；结果仍是候选，应用层授权和
selected revision 回查由下方 GraphRetrieval / GraphTraversal 完成。

本片不回收未传入的历史 revision 或重新分块后旧 Chunk，不写图向量，不实现图查询生成。

## 图向量与同一个 GraphIndexer

实体和关系按来源独立写入两个 dense collection，索引文本包含 Ontology 的标识/业务说明、类别/谓词说明及当前来源描述。不跨来源摘要，不索引
contextual_context 或原文。连接和 embedding 配置由装配入口提供：

```python
from rag.backends.qdrant import QdrantVectorStorage
from rag.ingestion.indexers import GraphIndexer

entity_vectors = QdrantVectorStorage(
    client=qdrant_client, collection="graph_entity_source_vectors_v4",
    dense_vector_size=EMBEDDING_DIMENSIONS, sparse_vector_name=None,
)
relationship_vectors = QdrantVectorStorage(
    client=qdrant_client, collection="graph_relation_source_vectors_v4",
    dense_vector_size=EMBEDDING_DIMENSIONS, sparse_vector_name=None,
)
for vectors in (entity_vectors, relationship_vectors):
    await vectors.ensure_collection(
        metadata_index_fields=["resource_id", "content_revision", "chunk_id", "ontology_name"],
    )

# 两种 writer 由一个 Indexer 按拓扑→向量顺序执行，不增加第二个 Indexer。
indexer = GraphIndexer(
    ontology=ontology, writer=topology,
    embedding_client=embedding_client,
    entity_writer=entity_vectors, relationship_writer=relationship_vectors,
)
await indexer.index(stored_chunks)
```

仅拓扑时只注入 writer；仅向量时省略 writer 并注入完整向量依赖组。当前批全部 embedding
成功后才删旧向量并写入，空事实清除该来源。跨集合/数据库不补偿，部分失败通过重跑恢复；输入解析和来源校验只做一次。调用方保证同资源串行。

## repair 图向量与仅投影模式

--with-graph-vectors 独立启用图向量，CLI 参数及环境变量如下。URL、embedding 配置和正整数维度必填，Qdrant key 可选，两个集合不得相同：

```text
RAG_GRAPH_REPAIR_QDRANT_URL
RAG_GRAPH_REPAIR_QDRANT_API_KEY
RAG_GRAPH_REPAIR_EMBEDDING_BASE_URL
RAG_GRAPH_REPAIR_EMBEDDING_API_KEY
RAG_GRAPH_REPAIR_EMBEDDING_MODEL
RAG_GRAPH_REPAIR_EMBEDDING_DIMENSIONS
RAG_GRAPH_REPAIR_ENTITY_VECTOR_COLLECTION    # 默认 graph_entity_source_vectors_v4
RAG_GRAPH_REPAIR_RELATION_VECTOR_COLLECTION  # 默认 graph_relation_source_vectors_v4
```

CLI 对应
--qdrant-url、--qdrant-api-key、--embedding-base-url、--embedding-api-key、--embedding-model、--embedding-dimensions、--entity-vector-collection、--relation-vector-collection。比如已配好维护环境后：

```powershell
.venv/Scripts/python.exe services/rag/scripts/repair_graph.py `
    --ontology-file services/rag/demo/graph-ontology.yaml `
    --resource-id RESOURCE_ID `
    --projection-only --with-graph --with-graph-vectors
```

仅投影模式不需要 instruction 配置、不调用抽取模型、不保存 Chunk，但图向量需要 embedding；至少启用一种投影，不可同时指定
force/gleaning。省略 --projection-only 则保持抽取→状态复核→事实保存→GraphIndexer，缓存复用仍重写所有启用投影。图向量失败保留事实和已完成拓扑，继续其他资源且最终非零退出。全部客户端关闭。

## GraphRetriever 种子组件与 Common Pipeline

```python
from common.utils.retrieval import RetrievalPipeline, RrfFusion
from rag.core.schema.filters import MetadataFilter, MetadataFilters
from rag.retrievers import GraphRetrievalMode, GraphRetriever

retriever = GraphRetriever.from_storage(
    entity_reader=entity_vectors,
    relationship_reader=relationship_vectors,
    embedding_client=embedding_client,
    mode=GraphRetrievalMode.HYBRID,
    entity_top_k=30, relationship_top_k=30, candidate_limit=60,
    filters=MetadataFilters([MetadataFilter("ontology_name", ontology.domain)]),
    fusion=RrfFusion(),  # 省略时使用 Common RoundRobin。
)

pipeline = RetrievalPipeline(
    retriever=retriever,
    prepare_candidates=prepare_graph_candidates,
    reranker=graph_reranker,
)
```

上面的回调示意说明组件边界；完整用例使用下方 GraphRetrieval，无需调用方重新实现准备逻辑。准备回调必须在同一
ResourceSnapshot 下先用 SnapshotReader 授权回查 Chunk，核对向量的 resource/revision、记录 ID、fact_id 与当前事实输入的
fact_sha256/graph_version，再消费 parse_graph_facts。仅对合法种子扩展一跳，并再次授权及核对关系/端点来源。后端过滤不能代替这一步，GraphMatch
不能直接作为 MCP 业务结果。

local 仅实体路线，global 仅关系路线，单路只需对应 reader，candidate_limit 就是路内预算；单路不接受
entity_top_k/relationship_top_k。hybrid 融合两路候选，不包含普通 Chunk 检索；mix 是否排期待定。每次直接 embedding query
一次，两路共用该向量并发读取。候选身份为 kind:record_id，同逻辑事实不同来源不会提前合并。组件不读
Mongo/Neo4j，不保存请求快照，不做生成或输出 token 裁剪；完整图材料用例已实现，见下方装配。

## 本体构造与事实读取

```python
node = ontology.create_node(
    name="张三", category="Person", source_id=chunk.chunk_id,
    description="当前块支持的人员描述",
)
nodes, edges = ontology.parse_chunk_facts(chunk)
# 成功缓存要求全部合法、无重复；损坏或非法项直接拒绝。
nodes, edges = ontology.parse_chunk_facts(chunk, strict=True)
```

本体对象绑定构造、写入与缓存的约束规则，不写入 GraphNode/GraphEdge 实例。字段仍为普通事实，可通过 asdict 写入 metadata。查询使用
parse_graph_facts 解析结构/来源和关系端点，不装配当前 Ontology 反向限制读取。

## 三种授权图检索

```python
from rag.application.acl.models import PermissionScope
from rag.application.graph import GraphRetrieval, GraphTraversal
from rag.application.snapshot import ResourceSnapshot
from rag.retrievers.graph import GraphRetrievalMode

# 各 reader/client 已由调用方连接；不创建 Container，不绑定查询侧 Ontology。
graph_retrieval = GraphRetrieval(
    snapshot_reader=snapshot_reader,
    graph_reader=topology,
    embedding_client=embedding_client,
    entity_reader=entity_vectors,
    relationship_reader=relationship_vectors,
)
snapshot = ResourceSnapshot(PermissionScope(user_id=trusted_user_id))
for mode in GraphRetrievalMode:
    hits = await graph_retrieval.retrieve(
        "张三的任职关系", snapshot=snapshot, mode=mode, top_k=5,
    )
    for hit in hits:
        print(hit.rank, hit.score, hit.evidence.kind, hit.evidence.fact_id)
        for entity in hit.evidence.entities:
            print(entity.id, entity.source_id, entity.description, entity.relations)
        for source in hit.evidence.sources:
            print(source.chunk_id, source.section_path, source.raw_text)
```

local 是实体种子及其一跳；global 是关系种子及支持该关系的同来源端点，不收集其他文档中仅描述端点的材料；hybrid
融合两路，一个逻辑种子一个证据组。top_k 限制组数，默认候选总数 60，hybrid 每路 30。GraphHit 不返回向量、上下文或任意 Chunk
metadata，不裁剪来源原文。

向量候选与全部拓扑来源经同一快照授权回查，事实从当前 Mongo metadata 解析，不以 Neo4j 描述替代原文。当前事实输入摘要
fact_sha256 与 graph_version 不符时丢弃旧向量；text_sha256 仍记录实际索引文本。查询不读取本体配置，也不按单一本体排除其他已写入事实。

需要限定事实本体时，GraphRetrieval.retrieve 和 GraphTraversal.traverse 均可传
`filters=MetadataFilters([MetadataFilter("ontology_name", "work-demo")])`。该条件与 resource_id/content_revision 一样支持字符串
EQ、字符串列表 IN 及 AND/OR/NOT，约束向量、拓扑和当前 Mongo 事实；不是加载 Ontology 或解析实体 ID。拓扑来源的 ontology_name 由
GraphIndexer 写入，旧来源先用 `--projection-only --with-graph` 重建。筛选后的合法实体即使没有关系也返回，不在扩展工具里额外转换证据。

## 普通混合检索选实体与逐跳探索

```python
from rag.application.retrieval import VectorRetrieval

hybrid = VectorRetrieval(
    snapshot_reader=snapshot_reader,
    vector_reader=chunk_vectors,
    embedding_client=embedding_client,
)
chunk_hits = await hybrid.retrieve("张三", snapshot=snapshot)

# 实体引用只有逻辑 ID 与类别关系提示；来源身份在外层 ChunkHit。
for hit in chunk_hits:
    for seed in hit.seed_entities:
        print(seed.id, [(option.relation_type, option.direction) for option in seed.relations])

traversal = GraphTraversal(snapshot_reader=snapshot_reader, graph_reader=topology)
# 演示调用方明确选一个 seed 和一个类别允许的关系；不自动遍历其他候选。
selected = next((seed for hit in chunk_hits for seed in hit.seed_entities if seed.relations), None)
if selected is not None:
    option = selected.relations[0]
    first = await traversal.traverse(
        [selected.id], snapshot=snapshot,
        direction=option.direction, relation_types=[option.relation_type],
    )
    # 调用方看到第一跳端点与它的关系提示后，再明确选择下一步。
    reached = next((entity for group in first for entity in group.entities
                    if entity.id != selected.id and entity.relations), None)
    if reached is not None:
        next_option = reached.relations[0]
        second = await traversal.traverse(
            [reached.id], snapshot=snapshot,
            direction=next_option.direction, relation_types=[next_option.relation_type],
        )
```

例如人员引用可以是以下形状：

```json
{
  "id": "work-demo:Person:张三",
  "relations": [{"relation_type": "works_at", "direction": "outgoing"}]
}
```

relations 由 Ontology.create_node 在写入时从 category/RelationSpec 整理，随实体普通字典保存，不表示可见事实一定存在该关系。模型选择允许关系后，查不到可见事实就返回空
relationships，不补造事实。GraphEvidence 的实体视图还带 source_id/description，也不重复名称、本体和类别。

traversal 只有一跳，不提供 depth；direction=None 表示 BOTH，relation_types=None 表示不限制谓词，[]
不读关系。读取不会按写入关系提示反向限制遍历。同一调用默认批量读取最多 100 个逻辑关系候选，已选关系的合法来源完整返回，过滤后不补读取。继续调用仍重新回查事实；复用
snapshot 保持同一次编排的首次资源版本与可见性决定。

旧 Mongo 实体未保存 relations 时提示为空；抽取策略版本已升级，正常事实 repair 会重算旧指纹补齐提示。仅 projection-only
不会改变旧 Mongo 事实；旧图向量缺少 fact_sha256 时应重建图向量投影。维护脚本仍由调用方保证同资源写入串行。
