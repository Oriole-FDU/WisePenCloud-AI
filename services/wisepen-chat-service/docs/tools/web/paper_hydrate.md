# paper_hydrate

实现入口：`src/chat/application/tools/web_tools/paper_hydrate_tool.py`

`paper_hydrate` 使用 OpenAlex 补全论文元数据。它只做结构化水合，不读取 PDF，不解析文档，不抓网页正文。

## 何时使用

- 已经有明确论文信号，例如 DOI、OpenAlex id 或具体论文标题。
- 需要补全作者、venue、abstract、open access、引用数或 landing url 等更细致信息。
- 搜索结果已经明确是论文，且结构化元数据会显著提升下一步判断。

## 参数

| 参数 | 类型 | 规则 |
| --- | --- | --- |
| `openalex_id` | `string` | 优先使用。支持 `W...` 或 `https://openalex.org/W...`。 |
| `doi` | `string` | 次优先。支持裸 DOI、`doi:`、`https://doi.org/...`。 |
| `title` | `string` | 已知论文标题时使用。 |
| `candidate_title` | `string` | 仅作备选标题。 |

## 输出

返回 `HydratedPaper`：

- `status`
- `title`
- `authors`
- `year`
- `venue`
- `doi`
- `openalex_id`
- `abstract`
- `landing_url`
- `pdf_url`
- `open_access`
- `cited_by_count`
- `concepts_or_topics`
- `source_updated_at`

## 边界

- 只使用 OpenAlex。
- 不使用 arXiv。
- 不下载 PDF。
- 不调用 `document_parse`。
- 不调用 `web_fetch`。
- `title` 低置信或多候选时返回 `partial`。
- 查不到返回 `not_found`，API 异常返回 `failed`。
