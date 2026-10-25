# Employment：公司内部合同扩展示例

[employment 源码](../src/rag/extensions/employment/container.py) 展示业务校验、完整摄入、专用向量索引、图工具及只读
API。通用开发方式见[扩展指南](extensions.md)，身份/metadata 合同见[模型](models.md)和[抽象](abstractions.md)。样例不会自动导入。

## 1. 输入、校验与完整链

[contracts.jsonl](../src/rag/extensions/employment/contracts.jsonl)
是北辰科技四份独立合同：李明先后入职研发部和产品部，陈晨、王小雨入职研发部。单份事件见 [document-ready.json](../src/rag/extensions/employment/document-ready.json)。

事件为 type=employment，metadata 仅 employee/start_date，原样入 Document 并逐 Chunk 深复制。EmploymentMetadataTransformer
只读校验，日期解析/首尾空白处理不回写事实；不规范化员工大小写、内部空白或别名。year 仅由专用向量 factory 从日期派生。

```text
平台 type 路由 → MarkdownChunker → EmploymentMetadataTransformer
→ 平台不带上下文链的 Transformers → GraphExtractor
→ ChunkFactsIndexer → 普通 VectorIndexer → GraphIndexer → contract_indexer
→ 平台事实回查 → apply
```

当前 [build_pipeline](../src/rag/extensions/employment/container.py) 显式使用 pipeline_without_context ()，不随平台
ENABLE_CONTEXTUALIZE 添加上下文。若业务需要上下文，应在扩展组合代码中明确选择带上下文链。

本体为 [ontology.yaml](../src/rag/extensions/employment/ontology.yaml)，domain=employment，实体 Person/Department，有向
in_department。部门关系只从正文抽取，不从 metadata 推断。身份和查询不绑定本体的规则见[图用例](usecases-graph.md)。

## 2. 启用与专用投影

在[平台配置](platform-settings.yaml)基础上配齐：

```yaml
EXTENSIONS: [employment]
ENABLE_GRAPH_STORAGE: true
NEO4J_URI: neo4j://localhost:7687
NEO4J_USERNAME: neo4j
NEO4J_PASSWORD: replace-me
INSTRUCTION_BASE_URL: https://your-model-endpoint/v1
INSTRUCTION_API_KEY: replace-me
INSTRUCTION_MODEL: your-instruction-model
FROM_SOURCE_SECRET: replace-with-private-gateway-secret
```

KAFKA_ENABLED=false 可关闭 broker，扩展、读取和 GC
仍装配。缺图/指令配置或网关密钥时完整装配失败；启动不调用真实模型探测。正常入口见[运行说明](deployment.md)。

专用集合默认 employment_contract_vectors_v4，dense-only，embedding 模型/维度与平台一致，record_id=chunk_id。普通集合及两个图来源集合照常写入。

| payload 字段     | 字段索引 | 来源                                     |
|------------------|----------|------------------------------------------|
| resource_id      | KEYWORD  | 通用 VectorIndexer 从 Chunk 合并         |
| content_revision | 不声明   | 同上，用于当前版本核验                   |
| year             | INTEGER  | employment_projection 从 start_date 派生 |
| employee         | KEYWORD  | 业务 DTO 校验值，精确匹配                |

[metadata.py](../src/rag/extensions/employment/metadata.py) 的 factory 仅返回 employee/year；Document/Chunk 不补写
year。业务变化提高 version，已发布同身份不会重建。collection 可在 EmploymentContainer (platform=..., collection=...)
覆盖，索引与查询须绑定同一个 storage。

## 3. 可信维护入口导入样例

下面代码供已经完成初始化/扩展登记的维护或测试入口使用，真实运行会调用模型并写库：

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

path 指向源码 contracts.jsonl，同资源发布串行。正常业务可交给 Kafka 消费者；没有 publish HTTP 接口。样例事件不授予
ACL，缺可见权限时查询空结果是正确行为。样例 ID 是可读字符串，不满足真实上游 ACL 同步器的 ObjectId 约束；端到端接业务时需使用真实资源
ID 和权威 ACL，测试由替身提供权限。

## 4. 工具与 API

[EmploymentTools](../src/rag/extensions/employment/tools.py) 将年份闭区间/员工条件映射为 MetadataFilters，调用通用
VectorRetrieval (mode=DENSE)，返回完整 ChunkHit。员工/日期从 hit.metadata.employee/start_date 获取，没有 employment 包装层、专用
prepare 或二次筛选。图工具只加 ontology_name=employment 和通用参数。

API 挂载在 `/rag/extensions/employment`：

| 方法/相对路径                        | 输入与返回                                                                                      |
|--------------------------------------|-------------------------------------------------------------------------------------------------|
| POST /contracts/search               | query、year_from/year_to、employee、top_k=10、candidate_limit=60 → list[ChunkHit]               |
| POST /graph/search                   | query、mode=hybrid、top_k=10（1–60）→ GraphRetrievalResult（entities、relationships、chunks）   |
| POST /graph/traverse                 | node_ids、direction=outgoing（可 incoming/both/null）→ GraphTraversalResult；固定 in_department |
| GET /contracts/{resource_id}/outline | 可见目录 → DocumentOutline，不可见 404                                                          |
| POST /contracts/sections             | section_ids、scope=own/subtree（默认 own）→ list[SectionText]                                   |

search 预算要求 candidate_limit≥top_k，年份为 1–9999 且下界≤上界。API DTO 禁止额外字段。每次请求从可信 Header 创建独立
snapshot；子应用 /docs、/openapi.json 也经网关校验。

以下为 bash/curl 语法，仅演示可信网关请求：

```bash
curl -X POST http://localhost:19906/rag/extensions/employment/contracts/search \
  -H 'Content-Type: application/json' \
  -H 'X-From-Source: <gateway-secret>' -H 'X-User-Id: <user-id>' \
  -d '{"query":"软件研发的内部合同","year_from":2022,"year_to":2024,"employee":"陈晨","top_k":10}'
```

FROM_SOURCE_SECRET 必须保密，部署限制后端访问，身份由网关注入，不能交给客户端自行声明。缺/错误网关令牌为 404，未 ready 为
503，缺可信用户为 401，输入 DTO 错误为 422；存储、embedding 和权威事实损坏异常传播，不统一包装成输入错误。

## 5. 两次显式一跳

从命中 seed_entities 或图结果选择 `employment:Person:李明`，先向 /graph/traverse 发：

```json
{"node_ids":["employment:Person:李明"],"direction":"outgoing"}
```

四份来源都可见且事实成功生成时，返回研发部和产品部。再选返回的研发部 ID 发：

```json
{"node_ids":["employment:Department:研发部"],"direction":"incoming"}
```

可返回李明、陈晨、王小雨的独立合同贡献；隐藏来源不会贡献事实。两个 HTTP 请求各自新快照；Python 同次编排可显式共享。无自动多跳，合法孤立实体仍保留。图检索与
traversal 都按逻辑 ID 收拢，实体与关系都只返回 id/sources，各合同描述放入对应对象的 sources，沿 sources[].chunk_id
读取原文。ChunkHit.seed_entities.relations 来自全图真实可见连接，不受合同年份/员工过滤约束；图搜索的 top_k 只限制来源
chunks 数。核心 GraphNode.relations 仍是写入时的本体允许提示，不再进入图实体结果。

本体条件也允许共享本体的其他可见资料，不要求其 metadata 带 employee/start_date。year/employee
只用于合同检索，不是图条件。旧拓扑本体属性重建见[运维手册](operations.md)。

## 6. 专用向量维护与失败

全部必需 Indexer 成功才 apply；专用写入失败保留已有事实/普通和图投影、旧 applied，同 staged 重跑恢复。已 applied 身份不能靠
publish 重写专用集合，通用图 repair 也不重建它。

在同资源写任务串行的维护窗口使用：

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
    # 写前校验全部业务 payload 并复核状态；不改写事实或发布状态。
    for chunk in chunks:
        employment_projection(chunk)
    if await platform.publication_store().get_states([resource_id]) != [state]:
        return False
    await employment.contract_indexer().index(chunks)
    return True
```

该有限示例只重写当前 revision 既有 Chunk 的专用向量，会调用 embedding，不抽取、不写 Mongo、不分块、不处理全历史清理；不等同于两个通用
repair 的完整 Document 身份核对流程。失败保留部分写入，由维护入口记录/重跑。

平台 GC 已登记专用集合，永久销毁时清理其全部
revision；停用前需完成数据回收。替身测试见 [test_employment_extension.py](../tests/application/test_employment_extension.py)、[文档示例测试](../tests/test_extensions.py)
；不代表真实后端/模型已联调。
