# Employment：公司内部合同完整扩展示例

本包替代原 demo，覆盖业务输入、完整入库、专用 Qdrant 索引、授权检索、图工具和挂载 API。样例都是虚构合同，不会自动写入数据库。

## 1. 从代码看整体链路

```text
DocumentReadyEvent(type=employment, metadata={employee, start_date})
 → 平台唯一 publication 按 type 路由
 → MarkdownChunker（深复制 metadata）
 → EmploymentMetadataTransformer（只读校验日期/非空名称）
 → 可选平台 Contextualizer → GraphExtractor（本包 Ontology）
 → ChunkFactsIndexer → 普通 VectorIndexer
 → GraphIndexer（Neo4j + 实体来源向量 + 关系来源向量）
 → 本包 contract_indexer（专用 dense 向量 + employee/year 增量）
 → 平台事实回查 → apply
```

先读 [container.py](container.py)，再读 [metadata.py](metadata.py)、[通用 VectorRetrieval](../../application/retrieval.py)、[tools.py](tools.py)、[api.py](api.py)。通用开发规范见 [extensions.md](../extensions.md)。额外集合、API 是本包声明的实际装配产物，生命周期/GC 是平台的消费者，不需要业务包维护 Kafka 或销毁流程。

## 2. 输入数据与权威来源

[document-ready.json](document-ready.json) 是单份事件，[contracts.jsonl](contracts.jsonl) 是北辰科技内部的四份独立合同：李明首次入职研发部、后来再次入职产品部；陈晨和王小雨在研发部。部门信息只出现在正文，不由 metadata 提供。

```json
{
  "resourceId": "employment-contract-001",
  "version": 1,
  "type": "employment",
  "content": "# 公司内部劳动合同\n\n雇员：李明。公司：北辰科技。所属部门：研发部。\n\n李明于2020-03-01入职北辰科技，隶属于研发部，负责软件研发相关工作。双方约定遵守保密和劳动纪律。",
  "metadata": {
    "employee": "李明",
    "start_date": "2020-03-01"
  }
}
```

上游 metadata 只包含 employee/start_date，原样进入 Document.metadata；MarkdownChunker 对每个 Chunk 深拷贝。EmploymentMetadataTransformer 只检查业务字段，不用 model_dump 覆盖 Chunk，不把日期或员工名称规范化后回写。图抽取新增自己的图字段，原业务值仍保留。

year 从 start_date 派生，校验后的 employee 仅用于专用向量精确匹配；两者由 employment_projection 返回，不在 Mongo 中新增 year。本体只从正文抽取 Person、Department 和有向 in_department 关系。domain=employment，部门逻辑 ID 如 `employment:Department:研发部`；读取侧不绑定本体配置。

变更 type 或业务 metadata 须提高 version；同一已发布 revision 不重新构建。本体或配置变化也不自动重建历史。

## 3. 专用集合与启动配置

在平台现有 Nacos 配置（Mongo、Qdrant、embedding、固定 Qwen rerank）上追加：

```yaml
EXTENSIONS: [employment]
FROM_SOURCE_SECRET: replace-with-private-gateway-secret
ENABLE_GRAPH_STORAGE: true
NEO4J_URI: neo4j://localhost:7687
NEO4J_USERNAME: neo4j
NEO4J_PASSWORD: replace-me
INSTRUCTION_BASE_URL: https://your-model-endpoint/v1
INSTRUCTION_API_KEY: replace-me
INSTRUCTION_MODEL: your-instruction-model
# KAFKA_ENABLED: false 可关闭 broker；GC、读取组件和扩展仍装配。
```

使用正常入口 `python -m rag.main`（默认端口 19906，随 SERVICE_PORT 配置调整）。平台在连接生命周期内构造扩展并显式初始化集合/字段索引、挂载 API；全部必需初始化和消费者连接完成后 ready，不调用真实模型探测启动。缺少图/指令配置或网关密钥，Employment 完整装配失败并清理已创建连接。

专用集合默认 `employment_contract_vectors_v4`，dense-only，模型/维度和平台一致。索引由 VectorCollection 和 IndexableField 声明，三个字段索引为 resource_id KEYWORD、year INTEGER、employee KEYWORD。员工名称按校验值精确匹配，不处理大小写、内部空白或别名。year 支持整数范围。

VectorIndexer 的 metadata_factory 只提供业务增量，例如 `{"employee": "李明", "year": 2020}`。通用 Indexer 合并 resource_id/content_revision；这两个身份值始终取自 Chunk，factory 不必重复提供，也不能覆盖。专用向量最终保存四个 payload 字段。

**payload 字段与数据库字段索引是两件事。** content_revision 必须保存在 payload，通用检索用它核对候选和当前授权 Chunk 的版本。此工具没有按 revision 过滤，因而不为它声明 Qdrant 字段索引；删除该索引声明不删除 payload 字段。普通默认集合的身份投影合同也保持。

collection provider 支持外层覆盖：`EmploymentContainer(platform=platform, collection="my_employment_vectors")`。在源码 `_BUILDERS` 登记这样的构造器即可；集合名称不得与平台或另一扩展冲突。业务查询和索引必须注入同一个 storage。

## 4. 如何导入样例

样例使用平台现有 DocumentReadyEvent 格式，可交给正常 Kafka 发布消费者。这里只演示维护/测试组合入口的直接调用，**不会开放无写权限合同的 publish HTTP 接口**：

```python
# example: load-contracts
import json
from pathlib import Path
from rag.application.publication import DocumentReadyEvent


async def load_contracts(platform, path: Path):
    for line in path.read_text(encoding="utf-8").splitlines():
        event = DocumentReadyEvent.model_validate(json.loads(line))
        print(
            event.resource_id, await platform.publication().publish(event.to_document())
        )
```

应在平台已经完成初始化和扩展登记的可信维护入口调用，path 指向本包 contracts.jsonl。同资源发布串行；真实后台会调用模型及写库。ACL 仍由平台 ACL 同步机制提供，样例事件不含授予读取权限的字段；没有可见 ACL 的资源，查询返回空是正确行为。

## 5. 专用合同查询与完整返回

API 根路径 `/rag/extensions/employment`，子应用提供 `/docs` 和 `/openapi.json`，请求与返回合同使用实际 DTO/dataclass 生成（同样需要可信网关 Header）。下例是 bash/curl 语法；生产由可信网关注入 Header，secret 不放进客户端业务代码。身份和群组 Header 只在密钥正确时被信任。

```bash
curl -X POST http://localhost:19906/rag/extensions/employment/contracts/search \
  -H 'Content-Type: application/json' \
  -H 'X-From-Source: <gateway-secret>' -H 'X-User-Id: <user-id>' \
  -d '{"query":"软件研发的内部合同","year_from":2022,"year_to":2024,"employee":"陈晨","top_k":10}'
```

返回 `list[ChunkHit]`，每项包含完整正文 text、来源定位、rank/score、seed_entities 和当前授权 Chunk 的完整 metadata。员工与入职日期直接从 `hit.metadata["employee"]`、`hit.metadata["start_date"]` 获取，没有 employment 包装层。示例四份都可见时，上述条件命中陈晨的 2022 年合同。

[tools.py](tools.py) 的 search_contracts 只把年份与员工翻译为 MetadataFilters，再调用绑定专用集合的通用 VectorRetrieval(mode=DENSE)。后端执行过滤，通用用例负责授权和资源/revision/记录身份，之后进行 Common 重排。没有 EmploymentHit、专用 prepare 或重复业务筛选；普通正文用例不额外校验同 revision 的业务/文本摘要。业务 metadata 变更必须提高 version，图查询自己的事实核验仍保留。隐藏或旧版本候选不会进入重排，返回不做 token 裁剪。

## 6. 高级图工具与连续两次一跳探索

接口清单：

| 方法及相对路径 | 输入/行为 |
| --- | --- |
| POST `/contracts/search` | query、year_from/year_to、employee、top_k/candidate_limit |
| POST `/graph/search` | query、mode=local/global/hybrid、top_k |
| POST `/graph/traverse` | node_ids、direction=outgoing/incoming/both/null；固定 in_department |
| GET `/contracts/{resource_id}/outline` | 当前可见合同目录 |
| POST `/contracts/sections` | section_ids、scope=own/subtree；完整章节原文 |

三模式检索示例正文：`{"query":"李明的任职关系","mode":"hybrid","top_k":10}`。local 从实体种子扩展一跳；global 从关系种子返回同来源端点；hybrid 融合两路。search_graph 和 follow 都传入 `MetadataFilters([MetadataFilter("ontology_name", "employment")])`，直接返回通用用例结果。后端在候选预算前筛选本体，通用用例负责授权和当前事实回查；工具不二次读取或重组证据，不解析实体 ID。

本体筛选不要求来源具有 employment 业务 metadata；共享该本体的其他可见来源也可提供事实。合法实体没有可见关系时仍返回实体证据，relationships 为空。业务 DTO 校验留在写入边界，年份/员工筛选留在专用合同检索。

合同命中给出 `seed_entities[].id` 及类别允许的 relations。选择 `employment:Person:李明`，首次 POST `/graph/traverse`：

```json
{"node_ids":["employment:Person:李明"],"direction":"outgoing"}
```

四份合同都可见时，返回李明到研发部、产品部的两条有向隶属关系。选择返回部门 `employment:Department:研发部`，第二次：

```json
{"node_ids":["employment:Department:研发部"],"direction":"incoming"}
```

返回李明、陈晨、王小雨到研发部的关系及各来源完整原文。隐藏陈晨的合同后不再返回该贡献；陈晨也不能靠隐藏来源成为种子。每次 API 请求重新构造快照，单请求复用首次可见性决定；不是自动多跳，也不裁剪共享节点的来源描述。

year/employee 条件目前只用于专用合同查询，不承诺 `/graph/search` 自动接受业务范围。扩展更复杂的时间图工具时需明确证据范围与候选预算，继续核对当前权威业务字段。

旧 Neo4j 来源未保存 ontology_name 时，本体筛选不会匹配它。使用通用 repair_graph 的 `--projection-only --with-graph` 和本包 ontology.yaml 重建拓扑来源属性；沿用脚本的资源、Mongo 和 Neo4j 配置，不调用抽取模型、不保存 Chunk。图向量原本已有本体字段，此次不要求重建向量。

## 7. 维护、部分失败和 GC

全部必需 Indexer 成功后才 apply。专用向量失败时 Mongo 事实和已完成普通/图投影保留，旧 applied 不变；恢复后重跑同一 staged 版本可覆盖恢复。已经发布的 revision 会跳过构建，因此不能靠普通 publish 修复该专用投影。

仅重建专用向量时，在同资源写入串行的维护窗口使用本包公开 `contract_indexer()`，不借 Graph repair 重放业务流水线：

```python
# example: rebuild-contract-vectors
from rag.extensions.employment.metadata import employment_projection


async def rebuild_contract_vectors(platform, employment, resource_id):
    states = await platform.publication_store().get_states([resource_id])
    if not states or states[0].destroyed or states[0].applied_revision is None:
        return False
    state = states[0]
    chunks = await platform.content_storage().get_revision_chunks(
        resource_id=resource_id,
        content_revision=state.applied_revision.content_revision,
    )
    # 在写入前完成全部业务 payload 校验，保留当前完整 Chunk，不改写事实。
    for chunk in chunks:
        employment_projection(chunk)
    if await platform.publication_store().get_states([resource_id]) != [state]:
        return False
    await employment.contract_indexer().index(chunks)
    return True
```

该维护示例只索引当前 revision 的既有 Chunk，不重分块、不调用 instruction、不写 Mongo、不 stage/apply；会调用 embedding。无跨库事务，失败保留部分写入，调用方决定记录/重跑。它不做全历史删除或更换分块后的全面回收。

平台已将专用 storage 纳入 GC，销毁资源后按 resource_id 删除四套向量、图贡献、ACL 和全部内容，永久标记保留；新集合不需要自行写 GC。停用扩展之前应完成其数据回收，因为停用后配置不再声明该集合。

## 8. 可验证范围

[test_employment_extension.py](../../../../tests/application/test_employment_extension.py) 使用真实 Pipeline、Mongo adapter 的 BSON 内存往返、SnapshotReader、通用三模式图用例和 HTTP ASGI 调用；模型与后端 I/O 用替身。覆盖专用索引、当前业务值变化、权限/旧版本、两次一跳、API 身份、GC 及写入恢复。真实 Qdrant 索引验证只在显式 RAG_TEST_QDRANT_URL 配置后运行随机隔离 fixture，不会导入样例到业务库。
