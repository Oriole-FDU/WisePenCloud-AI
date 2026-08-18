# WisePen RAG v2 接口说明

本文档面向使用 RAG 服务的 API/MCP/Agent 调用方。它描述当前 v2 的稳定 HTTP 视图，不描述 Mongo、Qdrant、Neo4j 或 Redis 的内部实现。

## 基本约定

- 所有接口均为需要登录的 `POST` endpoint。
- 用户和群组权限来自服务端安全上下文，请求 body 不接收 `user_id` 或角色字段。
- 实际响应外层由平台统一包装为 `R<T>`；下文只展示 `T` 的内容。
- 请求中的 `resource_id`、`state_id`、`section_id`、`node_id` 都是不透明字符串，调用方应原样保存和回传。
- `page_range` 是模型可见的紧凑页范围，例如 `"1 - 3"`。没有页标记时该值为 `null`；启用 `exclude_none` 的 READ 响应会省略它。
- 内容读取只返回当前 applied revision。资源不可访问、revision 失效或证据核验失败时，接口返回错误，不返回旧 revision 的降级内容。
- `GET /health` 返回服务健康状态，`GET /docs` 提供 FastAPI OpenAPI 页面。

## 推荐调用顺序

`text
locateCandidate
      │
      ├── sections[].section_id ──> getSectionContent
      │
      └── nodes[].node_id ────────> expandGraph
                                      │
                                      └── evidence_sections[].section_id
                                          └──> getSectionContent
`

`section_id` 是确定性 READ 地址；`node_id` 是图导航锚点。不要用 Section 的标题替代 ID，也不要把 node ID 当作正文地址。

## `POST /internal/rag/locateCandidate`

根据自然语言问题做混合召回、排序、ACL 过滤和证据核验，并把命中提升为完整 ReadingBlock。

### Request

`json
{
  "session_id": "session_demo_1",
  "semantic_query": "连续降雨后积水迟迟不退，应先检查什么？",
  "lexical_query": "积水 入渗",
  "max_results": 5
}
`

`lexical_query` 可省略；`max_results` 默认为 10，范围为 1-20。LOCATE 始终在当前身份全部可读且已发布的资源中检索。

### Response

`json
{
  "state_id": "nav_demo_1",
  "retrieval_status": "relevant",
  "nodes": [
    {
      "node_id": "kn_demo_wisepen_rag",
      "label": "WisePen RAG",
      "kind": "Entity",
      "entity_type": "product"
    }
  ],
  "sections": [
    {
      "resource_id": "resource_demo_1",
      "section_id": "section_demo_surface",
      "title": "土壤表层",
      "section_path": "入渗与排水检查 > 土壤表层",
      "reading_blocks": [
        {
          "reading_block_id": "block_demo_1",
          "text": "土壤板结会降低入渗速度，并使表层积水消退时间延长。",
          "page_range": "1",
          "anchor_labels": [],
          "matches": [
            {
              "chunk_id": "chunk_demo_1",
              "source_ref_id": "source_ref_demo_1",
              "ranges": [{"start_offset": 0, "end_offset": 25}]
            }
          ]
        }
      ]
    }
  ]
}
`

`sections` 是检索视图，不是确定性 Section 正文。它返回完整 ReadingBlock，并保留 `section_id`、`title`、`section_path` 供后续 READ。

`retrieval_status` 取值为 `relevant`、`uncertain` 或 `irrelevant`。即使结果为空，调用方也应保留这一判定，不要自行把空数组等同于依赖失败。

## `POST /internal/rag/getDocumentOutline`

读取资源的目录和 applied revision 元数据，不读取正文。

### Request

`json
{"resource_id": "resource_demo_1"}
`

### Response

`json
{
  "resource_id": "resource_demo_1",
  "document_version": 3,
  "content_revision": "rev_demo_3",
  "total_length": 18420,
  "outline": [
    {
      "section_id": "section_demo_1",
      "title": "入渗与排水检查",
      "section_path": "入渗与排水检查",
      "page_range": "1 - 3",
      "children": []
    }
  ]
}
`

目录节点没有 `level`。调用方可通过 `children` 表达层级，并使用 `title` 作为短锚点、`section_path` 作为完整定位信息。

## `POST /internal/rag/getPageContent`

按页标记读取正文。返回值是以 page label 为 key 的对象；纯 `flat_text` 文档没有页标记时不能通过该接口伪造页。

### Request

`json
{
  "resource_id": "resource_demo_1",
  "page_labels": ["1", "2"]
}
`

### Response

`json
{
  "1": {
    "text": "这一页的权威正文……",
    "page_range": "1",
    "sections": [
      {
        "section_id": "section_demo_1",
        "title": "入渗与排水检查",
        "section_path": "入渗与排水检查"
      }
    ],
    "anchor_labels": []
  }
}
`

Page 视图中的 Section 只有导航锚点，不包含 Section preview，避免和页正文重复。

批量读取只返回实际存在的 page label。调用方如需识别缺失项，应比较请求 key 和响应 key。

## `POST /internal/rag/getSectionContent`

按 Section ID 读取直属正文和导航关系。该接口是确定性读取，不经过检索，也不返回 ReadingBlock。

### Request

`json
{
  "resource_id": "resource_demo_1",
  "section_ids": ["section_demo_1"]
}
`

### Response

`json
{
  "section_demo_1": {
    "title": "入渗与排水检查",
    "section_path": "入渗与排水检查",
    "text": "该 Section 的直属正文……",
    "page_range": "1 - 3",
    "anchor_labels": [],
    "navigation": {
      "next": {"section_id": "section_demo_2", "title": "季节性维护", "section_path": "季节性维护", "preview": "..."},
      "children": []
    }
  }
}
`

`navigation` 里的对象是 Section 锚点，不是正文。`text` 为空字符串是合法结果，表示该 Section 没有直属正文。

批量读取只返回实际存在的 Section ID；缺失 ID 不会生成带 `reason` 的占位对象。

## `POST /internal/rag/expandGraph`

从当前 navigation state 已发现的 node 出发，执行有界图遍历、路径排序、证据核验和原子状态扩展。

### Request

`json
{
  "session_id": "session_demo_1",
  "state_id": "nav_demo_1",
  "seed_node_ids": ["kn_demo_wisepen_rag"],
  "relation_types": [],
  "direction": "both",
  "max_depth": 2,
  "max_results": 10,
  "query": "WisePen RAG 如何通过图谱继续读取知识？"
}
`

`seed_node_ids` 必须已经存在于该 state；`query` 是本次图路径排序使用的显式意图；`max_depth` 支持 1-2。

### Response

`json
{
  "state_id": "nav_demo_1",
  "traversal_direction": "both",
  "seed_nodes": [
    {
      "node_id": "kn_demo_wisepen_rag",
      "label": "WisePen RAG",
      "kind": "Entity",
      "entity_type": "product",
      "role": "seed"
    }
  ],
  "discovered_nodes": [
    {
      "node_id": "kn_demo_graphrag",
      "label": "GraphRAG",
      "kind": "Entity",
      "entity_type": "technology",
      "role": "discovered",
      "mention_evidence": [
        {
          "resource_id": "demo-wisepen-rag",
          "reading_block_id": "block_graph_navigation",
          "quote": "GraphRAG 技术",
          "reading_block_range": {"start_offset": 15, "end_offset": 26}
        }
      ]
    }
  ],
  "paths": [
    {
      "path": "WisePen RAG -[USES]-> GraphRAG",
      "relations": [
        {
          "source": {
            "node_id": "kn_demo_wisepen_rag",
            "label": "WisePen RAG"
          },
          "predicate": "USES",
          "target": {
            "node_id": "kn_demo_graphrag",
            "label": "GraphRAG"
          },
          "relation_evidence": [
            {
              "resource_id": "demo-wisepen-rag",
              "reading_block_id": "block_graph_navigation",
              "quote": "WisePen RAG 使用 GraphRAG 技术补充实体关系导航",
              "reading_block_range": {"start_offset": 0, "end_offset": 34}
            }
          ]
        }
      ]
    }
  ],
  "evidence_sections": [
    {
      "resource_id": "demo-wisepen-rag",
      "section_id": "section_graph_navigation",
      "title": "二、图谱导航",
      "section_path": "WisePen RAG 导航架构说明 > 二、图谱导航",
      "reading_blocks": [
        {
          "reading_block_id": "block_graph_navigation",
          "text": "WisePen RAG 使用 GraphRAG 技术补充实体关系导航，使模型能够沿文档中的知识关系继续读取材料。",
          "page_range": "1",
          "anchor_labels": []
        }
      ]
    }
  ]
}
`

契约重点：

- `seed_nodes` 和 `discovered_nodes` 分别标记本次输入节点与本次原子写入 state 的新节点，节点 role 只取 `seed` 或 `discovered`。
- `paths[].path` 按实际遍历顺序渲染为 section path 风格文本；`A -[P]-> B` 表示顺着关系方向遍历，`A <-[P]- B` 表示反向遍历。
- `paths[].relations` 保留精确的 `source -> predicate -> target` 事实方向，不随遍历方向反转；路径中已有 state 节点只在关系端点中出现。
- `relation_evidence` 直接证明对应关系；`mention_evidence` 证明 discovered 节点在当前可读正文中出现。
- `reading_block_range` 是 quote 在 ReadingBlock 文本中的 Python 字符半开区间。
- `evidence_sections` 是关系证据块与新节点 mention 证据块的去重并集，不包含 LOCATE 专属的 `chunk_id`、`source_ref_id` 或 `matches`。
- MENTION 是节点到资源正文的内部来源边，只用于 LOCATE seed 和节点证据查询，不参与路径遍历。
- 没有新增路径时固定返回 `discovered_nodes: []`、`paths: []`、`evidence_sections: []`，但保留 `state_id`。

`relation_types` 支持：

`text
ABOUT, RELATED_TO, PART_OF, USES, PRODUCES, DEPENDS_ON,
DERIVED_FROM, IMPLEMENTS, APPLIES_TO, CAUSES, COMPARES_WITH,
CONTRADICTS, EXTENDS, SUPERSEDES, LOCATED_IN, AUTHORED_BY, DEFINES,
EXPLAINS, EXAMPLE_OF, REQUIRES, CITES, PUBLISHED_IN, USES_DATASET,
USES_METHOD, SUPPLEMENTS, RETRACTS
`

## Kafka 事实输入

RAG v2 消费三类上游事实事件，用于维护派生索引。topic 名称可以通过配置覆盖，下表列出默认值。

| 默认 topic | Payload | 作用 |
| --- | --- | --- |
| `wisepen-document-ready-topic` | `{"resourceId":"...","version":1,"content":"..."}` | 构建并发布新的内容 revision |
| `wisepen-resource-acl-recalc-topic` | `{"resourceId":"..."}` | 从权威资源服务刷新 ACL 并同步检索/图后端 |
| `wisepen-resource-physical-destroy-topic` | `{"typedResourceIds":{"document":["..."]}}` | 删除资源的全部派生内容、检索、图谱和导航状态 |

Payload 允许额外字段，但上述字段必须满足类型和非空约束。永久非法 JSON/payload 会记录并提交 offset；application 或外部依赖失败时保留当前 offset 原地重试，不越过失败事件。

## 错误码

| code | 含义 | 常见原因 |
| --- | --- | --- |
| `42001` | 导航参数不合法 | seed 不在 state、请求字段越界 |
| `42002` | 导航状态不存在 | state 已过期或 session 不匹配 |
| `42003` | 导航状态已失效 | revision 切换、证据失效或权限变化 |
| `42004` | 资源内容不存在或不可访问 | resource 不存在、无权限或未发布 applied revision |
| `52001` | 知识导航服务不可用 | 检索、图遍历或导航编排依赖失败 |
| `52002` | 资源读取服务不可用 | 内容读取依赖失败 |

调用方应根据错误码决定重建导航 state、重新 LOCATE 或向用户报告资源不可用，不要把失败响应当成空结果。
