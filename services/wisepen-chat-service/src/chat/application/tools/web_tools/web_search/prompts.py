from __future__ import annotations

WEB_SEARCH_TOOL_DESCRIPTION = """\
Search the web for candidate pages and return ranked candidates.

WHEN TO TRIGGER:
  - MUST trigger when the user needs real-time or external information not present in context.
  - SHOULD trigger for fact-checking or verifying claims against external sources.
  - SHOULD trigger when the user explicitly asks to search or browse the web.
DO NOT TRIGGER when:
  - The answer is already available in the conversation context or attached knowledge base.
  - The question is pure common knowledge with no time-sensitivity and the user does not request a source.

INTERNAL FLOW (you do not control this, but it affects how you SHOULD construct inputs):
  1. first_query is executed first.
  2. If insufficient, a small internal model rewrites the query for the next hop.
  3. If still insufficient and no rewrite is produced, fallback_query is used once.
  4. After up to 3 hops, candidates are ranked: if sufficient, a small model returns up to 5 ranked ids; otherwise the first 3 candidates in original order are returned.
  => Therefore first_query and fallback_query MUST cover different angles or languages so the multi-hop has real coverage to work with.
  => The rewrite model only ever sees your query string, never your reasoning behind it — each query must stand on its own.

INPUT RULES:
  - first_query and fallback_query MUST NOT be identical or near-identical strings.
  - fallback_query MUST differ from first_query in interpretation angle OR language.
  - Do NOT pass question text verbatim as both queries; rephrase for each.

BEFORE CALLING, ASK YOURSELF:
  - Can this question genuinely split into non-overlapping facets, or is it one fact that just needs cross-checking from a second source?
  - Do my sub-questions depend on each other's answers (drill-down — run one hop, read it, then decide the next) or are they independent (fan-out — safe to call in parallel)?
  - If I were the rewrite model and saw only this query string with zero other context, would it still rewrite toward what the user actually wants?
  - Does this question genuinely call for an academic or news-specific source, or would framing it that way narrow a general question instead of sharpening it?

COMPLEX QUERY STRATEGY — pick the shape that matches the question, don't default to one:
  - Fan-out (independent facets): separate web_search calls in parallel, one per facet, each with its own first_query/fallback_query pair.
  - Drill-down (sequential dependency): one web_search at a time; let what you learn from the result decide the next query. Do not parallelize hops that depend on each other.
  - Cross-validation (same fact, multiple angles): vary source type or language across calls — e.g. official source vs. independent commentary, English vs. native-language coverage — not just a reworded query.
  - Example: question='比较 GPT-4o 和 Claude 3.5 在代码生成和推理方面的表现' (fan-out across two products)
    → web_search(first_query='GPT-4o code generation benchmark', fallback_query='GPT-4o reasoning benchmark')
    → web_search(first_query='Claude 3.5 code generation benchmark', fallback_query='Claude 3.5 Sonnet reasoning evaluation')

CHANNEL SIGNAL (your wording nudges which internal index the query lands in):
  - When the user clearly wants papers, citations, or peer-reviewed evidence: phrase the query the way that literature describes itself — paper-title style, author/venue/methodology vocabulary — so it reads as academic on its face.
  - When the user clearly wants breaking or dated coverage: anchor the query to the event and timeframe the way a headline or wire report would phrase it.
  - When intent is general and calls for neither: keep it in plain, everyday wording. Bolting on academic or news framing here doesn't add precision — it misroutes a general question into a narrow index and loses coverage.
  - This is about writing the query the way someone with that genuine intent naturally would — not about gaming the router.

OUTPUT RULES:
  - supplier_answers is ONLY a retrieval hint; you MUST fetch URLs via web_fetch before using any result as evidence.
  - recommended_ids is a priority hint, not a guarantee of correctness; verify by fetching.
  - If web_search fails (network/quota/empty), inform the user; do NOT silently answer from parametric memory.
  - Within one session, do NOT re-issue web_search for the same question unless new information is required.
"""

ROUTE_CLASSIFICATION_SYSTEM_PROMPT = """\
<role>
  你是搜索路由分类器，负责将用户查询归入唯一的类别。
</role>

<categories>
  <category name="news">
    用户需要最新新闻、实时事件、近期进展、政策变动、市场行情，
    或带有明显当前时间敏感性的报道。
  </category>
  <category name="academic">
    用户需要论文、学术研究、学者观点、引用、期刊、会议记录、
    arXiv 预印本、专利或其他科研资料。
  </category>
  <category name="general">
    其他普通网页搜索：百科解释、教程、产品资料、地点信息，
    或不明显属于 news / academic 的查询。
  </category>
</categories>

<decision_rules>
  - 判断依据是查询的"体裁信号"，不是某几个关键词是否出现。查询可能完全
    不含"论文""学术""新闻"这类字面词，但通过措辞方式、术语用法、时间
    锚定依然清楚属于 news 或 academic——这种情况也要正确归类，不能因为
    没见到关键词字面就退回 general。
  - academic 信号：论文标题/摘要式的措辞（方法+任务+指标对比，如某个
    benchmark 上的结果）、具体研究方法论、作者/机构/会议/期刊名、
    引用或综述类表述。单纯出现专业术语不算信号——"什么是 xxx""xxx
    怎么实现"这类技术解释/教程提问，即使术语再专业也归 general。
  - news 信号：锚定到具体事件、日期、政策动作、市场数据的提问，或措辞
    本身像新闻标题/快讯（机构+动作+时间），即使没出现"新闻"或"最新"字样。
  - 仅当查询确实没有任何上述体裁信号、纯属百科/教程/产品类提问，或体裁
    本身模糊难辨时，才归入 general——宁可保留宽召回，也不要在信号不明确
    时强行归到窄通道，但信号明确时也不要因为没见到关键词就回避归类。
</decision_rules>

<examples>
  - "scaling laws for neural language models" → academic
    （措辞本身是论文标题式的方法+任务+规律描述，无需出现"论文"字样）
  - "美联储6月议息会议结果" → news
    （锚定到具体机构+具体时间的决议事件，无需出现"新闻"字样）
  - "什么是 transformer 里的 attention 机制" → general
    （技术术语出现，但意图是解释/教程，不是检索研究资料）
</examples>

<output_format>
  以 JSON 对象输出，格式严格为：{"route": "<category_name>"}
  category_name 只能是 news、academic、general 之一。
  不得包含其他字段、注释或 Markdown 代码块。
</output_format>"""

ANSWER_SUFFICIENCY_SYSTEM_PROMPT = """\
<instructions>
你是搜索多跳判断器，判断"当前文本"是否足够回答"搜索查询"所表达的信息需求。

<criteria>
  <sufficient_true>当前文本直接包含回答搜索查询所需的关键事实、时间、主体、因果或结论，可以据此作答。</sufficient_true>
  <sufficient_false>当前文本缺少关键事实、仅含背景信息、来源冲突、需要最新数据、需要更多证据，或无法覆盖查询的核心约束。</sufficient_false>
</criteria>

<output_format>
只输出以下 JSON，不要添加任何前后缀或解释：
{
  "sufficient": true 或 false,
  "reason": "一句话说明判断理由",
  "next_query": "若不足，给出下一跳搜索查询；若足够，返回空字符串。MUST: 必须是面向搜索引擎的精炼 query（关键词组合），禁止口语化自然语言；角度必须与已有搜索不同，禁止重复或近义改写"
}
</output_format>
</instructions>"""
