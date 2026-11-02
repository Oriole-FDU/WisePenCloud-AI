# HTTP 服务规范与 API 手册

WisePen Scholar Service 遵循标准 RESTful 规范，所有对外学术接口均挂载于 `/scholar` 前缀下，查询编码服务挂载于 `/v1` 下。

---

## 一、 请求规范与通用约定

### 1. 基础地址与端口
- 默认监听地址：`http://127.0.0.1:19916`（可通过 Nacos / `.env` 中 `SERVICE_PORT` 调整）。
- 内部 RPC 调用：由 `wisepen-mcp-service` 通过 Nacos 动态寻址调用。

### 2. 请求头 (Headers)
| Header 名称 | 必填 | 说明 |
|---|---|---|
| `Content-Type` | 是 | 必须为 `application/json` |
| `X-From-Source` | 是 | 服务内部可信鉴权密钥，必须与配置中的 `FROM_SOURCE_SECRET` 一致 |
| `X-OpenAlex-Api-Key` | 否 | 用户自备 OpenAlex API 密钥（BYOK），未提供时禁用 OpenAlex 搜索 |
| `X-HuggingFace-Api-Key` | 否 | 用户自备 HuggingFace Token（BYOK），未提供时禁用 HF 搜索与发现 |
| `X-S2-Api-Key` | 否 | 用户自备 Semantic Scholar API 密钥，享受独立免排队的高速通道 |

### 3. 通用响应封包 (`R<T>`)
所有业务接口返回统一包装类型：
```json
{
  "code": 0,
  "msg": "success",
  "data": { ... }
}
```
- `code`: `0` 表示业务执行成功；非 `0` 表示异常状态（通常与 HTTP 状态码对齐，如 429, 503, 504）。
- `msg`: 描述信息，错误时展示可读的原因。
- `data`: 业务载荷，发生整次请求级失败时为 `null`。

---

## 二、 核心业务接口

### 1. 论文主题检索 (`POST /scholar/papers/search`)
- **接口功能**：根据研究问题、关键词、学术领域及发表日期区间检索论文。

#### 请求体示例 (`SearchRequest`)
```json
{
  "query": "graph neural networks for molecular property prediction",
  "field": "computer_science",
  "published_after": "2023-01-01",
  "published_before": "2024-12-31",
  "limit": 5,
  "source_scope": "default"
}
```
- `field`：可选领域枚举（默认 `"general"`），支持 `"computer_science"`, `"medicine"`, `"biology"` 等 13 个领域。
- `source_scope`：`"default"`（按领域配置顺序回退）或 `"multi_source"`（并发检索多源融合）。

#### 响应体示例
```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "papers": [
      {
        "arxiv_id": "2301.12345",
        "doi": "10.1145/1234567",
        "title": "A Unified Framework for Graph Neural Networks in Molecular Science",
        "abstract": "We propose a novel GNN architecture...",
        "authors": ["Alice Smith", "Bob Jones"],
        "publication_date": "2023-01-15",
        "year": 2023,
        "url": "https://arxiv.org/abs/2301.12345",
        "pdf_url": "https://arxiv.org/pdf/2301.12345.pdf",
        "source": "arxiv",
        "metadata": {
          "arxiv": {
            "categories": ["cs.LG", "stat.ML"],
            "journal_ref": null,
            "comments": "Accepted to ICML 2023"
          },
          "s2": {
            "citation_count": 85,
            "influential_citation_count": 12,
            "venue": "ICML",
            "tldr": "This paper presents a scalable molecular GNN."
          },
          "huggingface": {
            "upvotes": 42,
            "github_repo": "https://github.com/example/mol-gnn",
            "github_stars": 320
          }
        }
      }
    ],
    "execution": {
      "providers": [
        {
          "source": "arxiv",
          "status": "success",
          "latency_ms": 142.5,
          "error": null,
          "queued_ahead": null
        }
      ]
    }
  }
}
```

---

### 2. 论文精准标识符查询 (`POST /scholar/papers/lookup`)
- **接口功能**：依据 DOI、arXiv ID、OpenAlex ID 等稳定身份批量获取论文与富化元数据。

#### 请求体示例 (`LookupRequest`)
```json
{
  "identifiers": [
    "10.1038/s41586-020-2649-2",
    "2301.12345",
    "https://openalex.org/W2741809807"
  ],
  "field": "computer_science"
}
```

#### 响应体示例
```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "items": [
      {
        "identifier": "10.1038/s41586-020-2649-2",
        "status": "found",
        "paper": {
          "arxiv_id": null,
          "doi": "10.1038/s41586-020-2649-2",
          "title": "Language Models are Few-Shot Learners",
          "abstract": "Recent work has demonstrated substantial gains...",
          "authors": ["Tom B. Brown", "Benjamin Mann"],
          "publication_date": "2020-05-28",
          "year": 2020,
          "url": "https://doi.org/10.1038/s41586-020-2649-2",
          "pdf_url": "https://arxiv.org/pdf/2005.14165.pdf",
          "source": "openalex",
          "metadata": {
            "openalex": {
              "id": "W2741809807",
              "cited_by_count": 12500,
              "venue": "NeurIPS",
              "primary_topic": "Language Models and NLP"
            }
          }
        },
        "error": null
      },
      {
        "identifier": "invalid-doi-99999",
        "status": "not_found",
        "paper": null,
        "error": "Identifier not resolved in any provider"
      }
    ],
    "execution": {
      "providers": [
        {
          "source": "arxiv",
          "status": "cached",
          "latency_ms": 1.2,
          "error": null
        },
        {
          "source": "s2",
          "status": "success",
          "latency_ms": 230.1,
          "error": null
        }
      ]
    }
  }
}
```

---

### 3. 完整标题消歧匹配 (`POST /scholar/papers/match`)
- **接口功能**：通过输入论文的完整标题，严格解析对应论文实体（避免 LLM 幻觉与近似混淆）。

#### 请求体示例 (`MatchRequest`)
```json
{
  "titles": [
    "Attention Is All You Need",
    "Deep Residual Learning for Image Recognition"
  ],
  "field": "computer_science"
}
```

#### 响应体示例
```json
{
  "code": 0,
  "msg": "success",
  "data": {
    "items": [
      {
        "title": "Attention Is All You Need",
        "status": "found",
        "paper": {
          "arxiv_id": "1706.03762",
          "doi": "10.48550/arxiv.1706.03762",
          "title": "Attention Is All You Need",
          "abstract": "The dominant sequence transduction models...",
          "authors": ["Ashish Vaswani", "Noam Shazeer", "Niki Parmar"],
          "publication_date": "2017-06-12",
          "year": 2017,
          "url": "https://arxiv.org/abs/1706.03762",
          "pdf_url": "https://arxiv.org/pdf/1706.03762.pdf",
          "source": "arxiv",
          "metadata": { ... }
        },
        "error": null
      }
    ],
    "execution": {
      "providers": [
        {
          "source": "redis_title_index",
          "status": "success",
          "latency_ms": 0.8
        }
      ]
    }
  }
}
```

---

### 4. 学术社区热点发现 (`POST /scholar/papers/discover`)
- **接口功能**：追踪社区权威发布渠道的最新趋势与点赞排行。

#### 请求体示例 (`DiscoveryRequest`)
```json
{
  "field": "computer_science",
  "period": "daily",
  "sort": "trending",
  "limit": 10
}
```

---

## 三、 辅助与监控接口

### 1. 向量编码代理接口 (`POST /v1/embeddings`)
- **接口功能**：保持与 OpenAI API 兼容的 SPECTER2 文本查询嵌入接口，供内部单例调用。
- **请求体**：
  ```json
  {
    "model": "allenai/specter2_adhoc_query",
    "input": "biomedical natural language processing",
    "dimensions": 768,
    "encoding_format": "float"
  }
  ```
- **响应体**：
  ```json
  {
    "object": "list",
    "model": "allenai/specter2_adhoc_query",
    "data": [
      {
        "object": "embedding",
        "index": 0,
        "embedding": [-0.0124, 0.0451, ..., 0.0892]
      }
    ]
  }
  ```

### 2. 健康检查接口
- **主服务健康探针 (`GET /scholar/health`)**：
  - 检查 Redis 是否可连通（`PING`）以及 Qdrant 别名集合是否存在。
  - 成功返回 `200`（`{"status": "ready"}`）；任一依赖失败返回 `503`（`{"status": "not_ready"}`）。
- **查询编码模型健康探针 (`GET /health`)**：
  - 返回模型常驻就绪状态：`{"status": "ready", "model": "allenai/specter2_adhoc_query"}`。

---

## 四、 错误处理与 HTTP 状态码映射

| 状态码 | 触发条件 | 说明与客户端建议 |
|---|---|---|
| `200` | 正常成功 | 业务正常交付（即使部分项未找到，也保留在 `items` 中交付） |
| `400` / `422` | 参数格式或校验错误 | 如标识符列表超过 20 项、日期格式错误或请求了未知模型 |
| `429` | 触发并发或配额限流 | 如 S2 平台请求队列拥堵、前方排队批次超过阈值（`ProviderBusy`） |
| `503` | 基础能力不可用 | 如领域未配置 discoverer、缺少必填的搜索 BYOK 凭证或 Qdrant 存储离线 |
| `504` | 请求超时 | 执行总耗时耗尽了 `SCHOLAR_REQUEST_TIMEOUT_SECONDS`（默认 25 秒） |
