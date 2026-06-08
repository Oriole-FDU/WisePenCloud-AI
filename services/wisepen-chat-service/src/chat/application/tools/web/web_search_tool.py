import asyncio
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Set

from chat.application.infra.content_store.formatting import format_tool_content_receipt
from chat.application.infra.content_store.models import ContentReceipt
from chat.application.tools.common.evidence_ranking.models import (
    EvidenceRankResult,
)
from chat.application.tools.common.evidence_ranking.ranking import rank_evidence
from chat.application.tools.tool_content_store import ToolContentStore
from chat.application.tools.web.services.web_search.coordinator import SearchCoordinator
from chat.application.tools.web.services.web_search.enums import ProviderMode, SearchMode
from chat.application.tools.web.services.web_search.errors import (
    CustomSearchProviderUnavailableError,
    EmptySearchResultError,
)
from chat.application.tools.web.services.web_search.models import (
    CustomProviderCredential,
    SearchManyRequest,
    SearchResponse,
    WikipediaGroundingResult,
)
from chat.application.tools.web.services.web_search.provider_policy.service import SearchProviderConfig
from chat.application.tools.web.services.web_search.utils.domains import (
    count_unique_domains,
)
from chat.application.tools.web.services.web_search.utils.notes import add_note, deduplicate_notes
from chat.application.tools.web.services.web_search.utils.queries import normalize_queries
from chat.application.tools.web.utils.domains import normalize_domain, extract_domain
from chat.application.tools.web.utils.urls import canonicalize_url
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail


@dataclass(frozen=True, slots=True)
class OutputBudget:
    """表示当前组件。"""
    min_queries: int           # 最小查询变体数限制
    max_queries: int           # 最大查询变体数限制
    candidate_page_limit: int   # 候选页面上限
    source_display_limit: int   # 结果展示上限
    ranked_evidence_limit: int  # 排序证据上限
    suggested_fetch_limit: int  # 建议抓取上限


@dataclass(frozen=True, slots=True)
class DomainFilters:
    """表示当前组件。"""
    allowed_domains: List[str] = field(default_factory=list)
    blocked_domains: List[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CandidatePage:
    """候选页面契约。"""

    source_id: str
    title: str
    url: str
    domain: str


@dataclass(frozen=True, slots=True)
class Citation:
    """引文契约。"""

    source_id: str
    title: str
    url: str
    domain: str
    order: str = "reranked"


DEPTH_BUDGET_MAP: Dict[SearchMode, OutputBudget] = {
    SearchMode.FAST: OutputBudget(
        min_queries=1,
        max_queries=1,
        candidate_page_limit=0,
        source_display_limit=10,
        ranked_evidence_limit=3,
        suggested_fetch_limit=2,
    ),
    SearchMode.NORMAL: OutputBudget(
        min_queries=1,
        max_queries=2,
        candidate_page_limit=5,
        source_display_limit=15,
        ranked_evidence_limit=6,
        suggested_fetch_limit=3,
    ),
    SearchMode.DEEP: OutputBudget(
        min_queries=2,
        max_queries=4,
        candidate_page_limit=12,
        source_display_limit=10,
        ranked_evidence_limit=8,
        suggested_fetch_limit=5,
    ),
}


TOOL_DESCRIPTION = (
    "Searches the web with concurrent multi-query recall and returns candidate search evidence "
    "(titles, URLs, and snippets). It does NOT fetch or read full page bodies.\n\n"
    "Use web_search when the user needs current information, external web recall, source discovery, "
    "official documentation, comparisons, or candidate URLs for subsequent analysis.\n\n"
    "To maximize recall accuracy and depth, you are strongly encouraged to include both "
    "Chinese and pure English query variants (bilingual cross-validation) when applicable, "
    "especially for technical, academic, or global topics.\n\n"
    "CRITICAL REQUIREMENT FOR WIKIPEDIA GROUNDING: If you provide `wikipedia_keywords`, "
    "every keyword item MUST be strictly in English (ASCII words, names, or technical terms only) "
    "to ensure accurate cross-language concept alignment with global Wikipedia entries.\n\n"
    "Mode Selection Rules (Choose the most specific mode):\n"
    "- fast: Quick facts, definitions, official sites, or brief single-query overviews.\n"
    "- normal: Medium-complexity searches requiring a small candidate pool.\n"
    "- deep: Deep research, technical/engineering decisions, multi-source verification, or broad cross-language recall."
)

TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "queries": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "maxItems": 4,
            "description": (
                "Distinct query variants for multi-query concurrent recall. "
                "Budget rules: fast=exactly 1, normal=1-2, deep=2-4. "
                "Including both Chinese and English variants is highly recommended for optimal global recall."
            ),
        },
        "wikipedia_keywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 3,
            "description": (
                "Optional short entities or concepts for Wikipedia grounding. "
                "CRITICAL REQUIREMENT: Every item MUST be strictly in English (ASCII words or technical terms only) "
                "to ensure accurate cross-language alignment with global database entries. Do not pass full questions."
            ),
        },
        "mode": {
            "type": "string",
            "enum": ["fast", "normal", "deep"],
            "description": (
                "Explicit search depth. Follow the specific mode selection rules defined in the main tool description."
            ),
        },
        "objective": {
            "type": "string",
            "description": (
                "The user's actual core information goal. Queries are used for broad search recall, "
                "while objective is used by the backend to rank results and suggest which source_ids to analyze next."
            ),
        },
        "allowed_domains": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 10,
            "uniqueItems": True,
            "description": (
                "Optional domain allow-list applied after search recall. "
                "Use bare domains such as openai.com or docs.python.org."
            ),
        },
        "blocked_domains": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": 20,
            "uniqueItems": True,
            "description": (
                "Optional domain block-list applied after search recall. "
                "Use bare domains such as csdn.net or example.com."
            ),
        },
    },
    "required": ["queries", "mode"],
    "additionalProperties": False,
}


class WebSearchTool(BaseTool):

    def __init__(self, coordinator: SearchCoordinator, content_store: ToolContentStore):
        """初始化对象依赖。"""
        self._coordinator = coordinator
        self._content_store = content_store

    @property
    def name(self) -> str:
        """返回组件名称。"""
        return "web_search"

    @property
    def description(self) -> str:
        """返回组件说明。"""
        return TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        """返回工具参数结构。"""
        return TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:

        session_id = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        user_id = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        notes: List[str] = []

        # 模式预算与查询清洗。
        mode = SearchMode(kwargs["mode"])
        budget = DEPTH_BUDGET_MAP[mode]

        queries = normalize_queries(kwargs["queries"], limit=4, notes=notes)
        if not queries:
            return "[Tool Error] queries list contains no valid search terms after normalization."

        objective_raw = kwargs.get("objective") or ""
        objective = objective_raw.strip() if objective_raw.strip() else None

        domain_filters = _parse_domain_filters(kwargs)

        wikipedia_keywords_raw = kwargs.get("wikipedia_keywords") or []
        wikipedia_keywords = [k.strip() for k in wikipedia_keywords_raw if k.strip()]

        # 搜索源模式与自定义渠道凭证。
        provider_mode = ProviderMode.DEFAULT
        credential = None
        search_config: SearchProviderConfig | None = context.get("search_config")
        
        if search_config and search_config.provider_mode == ProviderMode.CUSTOM:
            provider_mode = search_config.provider_mode
            if not search_config.active_provider or not search_config.is_valid:
                return (
                    "[Tool Error] The current web search mode is custom, "
                    "but the configured custom search provider is unavailable.\n"
                    f"- Active provider: {search_config.active_provider}"
                    f"- Is Valid: {search_config.is_valid} "
                    f"- Error Message: {search_config.error_message} "
                    "Ask the user to recharge that provider API key, "
                    "replace the key, or switch back to default search mode."
                )

            if search_config.api_key is None:
                return (
                    "[Tool Error] The current web search mode is custom, "
                    "but the provider API key is missing after runtime credential loading.\n"
                    f"- Active provider: {search_config.active_provider}\n"
                    "Ask the user to reconfigure and verify that provider."
                )

            credential = CustomProviderCredential(
                provider=search_config.active_provider,
                api_key=search_config.api_key,
            )

        candidate_page_limit = budget.candidate_page_limit

        try:
            request = SearchManyRequest(
                queries=queries,
                mode=mode,
                provider_mode=provider_mode,
                user_id=user_id,
                wikipedia_keywords=wikipedia_keywords,
                custom_provider_credential=credential,
            )

            # 多查询召回后，再应用域名过滤。
            results = await self._coordinator.search_many(request)
            response = _apply_domain_filters(
                response=results.response,
                filters=domain_filters,
                notes=notes,
            )
            groundings = results.groundings

        except CustomSearchProviderUnavailableError as e:
            return (
                f"[Tool Error] The current web search mode is custom, "
                f"but the configured custom search provider is unavailable.\n"
                f"Current Provider: {e.provider}\n"
                f"Error Message: {e}\n"
                f"Ask the user to recharge that provider API key, "
                f"replace the key, or switch back to default search mode."
            )

        except EmptySearchResultError as e:
            return (
                f"[Tool Error] All search queries returned no results. "
                f"Please try different or more specific queries.\n"
                f"Current Queries: {e.queries}"
            )

        except Exception as e:
            log_fail(
                "web_search unexpected error",
                repr(e),
                session_id=session_id,
                mode=mode,
            )
            return "[Tool Error] Unexpected error while searching the web."

        if response is None:
            return "[Tool Result] Failed to search the web."

        # 候选页面用于后续网页抓取的来源标识模式。
        candidate_pages = _select_candidate_pages(
            response=response,
            limit=candidate_page_limit,
        )

        if not candidate_pages:
            add_note(
                notes,
                "No candidate source_ids were available for follow-up web_fetch calls.",
            )

        citations = [
            Citation(
                source_id=str(index),
                title=r.title.strip() or r.url or "(no title)",
                url=(clean_url := r.url.strip()),
                domain=extract_domain(clean_url),
                order="reranked",
            )
            for index, r in enumerate(response.results, 1)
            if r.url.strip()
        ]

        ranking_query_used = objective if objective else " ".join(queries)

        # 构造并缓存网页搜索证据包。
        artifact_text = _build_web_search_artifact_json(
            response=response,
            mode=mode,
            queries=queries,
            notes=notes,
            candidate_pages=candidate_pages,
            groundings=groundings,
        )

        metadata: Dict[str, Any] = {
            "content_kind": "web_search_evidence_pack",
            "mode": mode.value,
            "queries": queries,
            "source_order": "reranked",
            "suggested_next_tool": "web_fetch",
            "optional_rerank_tool": "evidence_rank",
            "ranking_query_used": ranking_query_used,
            "result_count": len(response.results),
            "candidate_page_count": len(candidate_pages),
            "unique_domain_count": count_unique_domains(response.results),
            "citations": [asdict(citation) for citation in citations],
        }
        if objective:
            metadata["objective"] = objective

        # 将内容放入缓存，不直接交给模型
        receipt: ContentReceipt = self._content_store.put_receipt(
            session_id=session_id,
            tool_name="web_search",
            source="; ".join(queries),
            text=artifact_text,
            content_type="application/json",
            metadata=metadata,
        )
        if receipt is None:
            return "[Tool Error] Failed to cache tool artifact."

        # 对搜索结果产物做初筛重排，生成建议抓取的来源标识。
        try:
            ranking_result: EvidenceRankResult = await asyncio.to_thread(
                rank_evidence,
                query=ranking_query_used,  # type: ignore
                content_ids=[receipt.content_id],
                session_id=session_id,  # type: ignore
                max_evidence=budget.ranked_evidence_limit,
                content_store=self._content_store,
            )
        except Exception as e:
            log_fail(
                "web_search evidence ranking",
                repr(e),
                session_id=session_id,
                mode=mode,
                content_id=receipt.content_id,
            )

            ranking_result = EvidenceRankResult(
                query=ranking_query_used,  # type: ignore
                content_ids_found=[receipt.content_id],
                notes=["Internal web_search evidence ranking failed."],
            )

        ranking_lines = _format_search_ranking_lines(
            search_content_id=receipt.content_id,
            response=response,
            ranking_query_used=ranking_query_used,  # type: ignore
            ranking_result=ranking_result,
            mode=mode,
            notes=notes,
        )

        if mode == SearchMode.FAST:
            visible_result = _format_response(
                response,
                mode=mode,
                queries=queries,
                notes=notes,
                candidate_pages=candidate_pages,
                source_display_limit=budget.source_display_limit,
                groundings=groundings,
            )
            return visible_result + "\n\n" + "\n".join(ranking_lines)

        return format_tool_content_receipt(receipt) + "\n\n" + "\n".join(ranking_lines)


def _parse_domain_filters(kwargs: Dict[str, Any]) -> DomainFilters:
    """解析并提纯域名过滤器。"""
    allowed_raw = kwargs.get("allowed_domains") or []
    blocked_raw = kwargs.get("blocked_domains") or []

    allowed_cleaned = [d for x in allowed_raw if (d := normalize_domain(x))]
    blocked_cleaned = [d for x in blocked_raw if (d := normalize_domain(x))]

    overlap = set(allowed_cleaned) & set(blocked_cleaned)
    if overlap:
        allowed_cleaned = [d for d in allowed_cleaned if d not in overlap]

    return DomainFilters(
        allowed_domains=allowed_cleaned,
        blocked_domains=blocked_cleaned,
    )


def _apply_domain_filters(
    response: SearchResponse,
    filters: DomainFilters,
    notes: List[str],
) -> SearchResponse:
    """针对搜索引擎响应结果的域名黑白名单过滤器。"""
    if not filters.allowed_domains and not filters.blocked_domains:
        return response

    allowed_set = {
        normalize_domain(d) for d in filters.allowed_domains if normalize_domain(d)
    }
    blocked_set = {
        normalize_domain(d) for d in filters.blocked_domains if normalize_domain(d)
    }

    filtered_results = []
    for result in response.results:
        domain = extract_domain(result.url)
        if not domain:
            continue

        if allowed_set and not any(
            domain == f or domain.endswith("." + f) for f in allowed_set
        ):
            continue

        if blocked_set and any(
            domain == f or domain.endswith("." + f) for f in blocked_set
        ):
            continue

        filtered_results.append(result)

    removed_count = len(response.results) - len(filtered_results)

    if removed_count:
        allowed_str = ", ".join(filters.allowed_domains) or "(none)"
        blocked_str = ", ".join(filters.blocked_domains) or "(none)"
        add_note(
            notes,
            f"Domain filters removed {removed_count} search results "
            f"(allowed_domains={allowed_str}; blocked_domains={blocked_str}).",
        )
    if response.results and not filtered_results:
        add_note(notes, "Domain filters removed all search results.")

    metadata = dict(response.metadata)
    metadata["domain_filters"] = {
        "allowed_domains": filters.allowed_domains,
        "blocked_domains": filters.blocked_domains,
        "removed_result_count": removed_count,
    }

    return SearchResponse(
        query=response.query,
        results=filtered_results,
        metadata=metadata,
        source=response.source,
    )


def _select_candidate_pages(
    response: SearchResponse,
    *,
    limit: int,
) -> List[CandidatePage]:
    """从搜索结果中选择可供 web_fetch 继续读取的候选页面。"""
    if limit <= 0:
        return []

    candidates: List[CandidatePage] = []
    seen: Set[str] = set()

    for i, result in enumerate(response.results, 1):
        url = result.url.strip()
        if not url or not (norm_url := canonicalize_url(url)) or norm_url in seen:
            continue

        seen.add(norm_url)
        candidates.append(
            CandidatePage(
                source_id=str(i),
                title=result.title.strip() or url or "(no title)",
                url=url,
                domain=extract_domain(url),
            )
        )
        if len(candidates) >= limit:
            break

    return candidates


def _build_web_search_artifact_json(
    *,
    response: SearchResponse,
    mode: SearchMode,
    queries: List[str],
    notes: List[str],
    candidate_pages: List[CandidatePage],
    groundings: List[WikipediaGroundingResult],
) -> str:
    """构造 web_search evidence pack artifact。"""
    candidate_ids = {page.source_id for page in candidate_pages}

    citations = [
        Citation(
            source_id=str(i),
            title=r.title.strip() or r.url or "(no title)",
            url=(clean_url := r.url.strip()),
            domain=extract_domain(clean_url),
            order="reranked",
        )
        for i, r in enumerate(response.results, 1)
        if r.url.strip()
    ]

    payload: Dict[str, Any] = {
        "content_kind": "web_search_evidence_pack",
        "mode": mode.value,
        "queries": queries,
        "source_order": "reranked",
        "summary": {
            "result_count": len(response.results),
            "unique_domain_count": count_unique_domains(response.results),
            "candidate_page_count": len(candidate_pages),
            "source": response.source,
            "domain_filters": response.metadata.get("domain_filters"),
        },
        "results": [
            {
                "source_id": str(i),
                "title": r.title.strip() or r.url or "(no title)",
                "urls": r.url.strip(),
                "domain": extract_domain(r.url),
                "snippet": r.snippet.strip(),
                "is_candidate_page": str(i) in candidate_ids,
            }
            for i, r in enumerate(response.results, 1)
        ],
        "candidate_pages": [asdict(page) for page in candidate_pages],
        "groundings": [
            {
                "keyword": gr.keyword,
                "title": gr.title,
                "extract": gr.extract,
                "urls": gr.url,
            }
            for gr in groundings
        ],
        "notes": deduplicate_notes(notes),
        "citations": [asdict(citation) for citation in citations],
    }

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _format_search_ranking_lines(
    *,
    search_content_id: str,
    response: SearchResponse,
    ranking_query_used: str,
    ranking_result: EvidenceRankResult,
    mode: SearchMode,
    notes: List[str],
) -> List[str]:
    """格式化精排结果和建议抓取 source_ids。"""

    ranked_payload = [
        {
            "source_id": ev.source_id,
            "title": ev.title,
            "domain": ev.domain,
            "urls": ev.url,
            "snippet": ev.excerpt,
            "lexical_score": round(ev.score, 4),
            "matched_reason": ev.matched_reason,
            "term_hit_stats": [
                {
                    "term": stat.term,
                    "total_count": stat.total_count,
                    "field_stats": [
                        {"field": f.field, "count": f.count}
                        for f in stat.field_stats
                    ],
                }
                for stat in ev.term_hit_stats
            ],
            "suggested_next_action": "web_fetch_by_source_id",
        }
        for ev in ranking_result.evidence
        if ev.evidence_type == "web_search_result" and ev.source_id
    ]

    suggested_source_ids = _build_suggested_fetch_source_ids(ranked_payload, mode=mode)
    output_notes = deduplicate_notes(notes + ranking_result.notes)
    provider_source = response.source or ""

    lines = [
        "[WebSearch Ranking]",
        f"search_content_id: {search_content_id}",
        f"ranking_query_used: {ranking_query_used}",
        f"result_count: {len(response.results)}",
        f"unique_domain_count: {count_unique_domains(response.results)}",
    ]
    if provider_source:
        lines.append(f"provider_source: {provider_source}")

    lines.append(
        f"suggested_fetch_source_ids: "
        f"{json.dumps(suggested_source_ids, ensure_ascii=False)}"
    )
    lines.append(
        "next_step: ranked search evidence is already included. Use "
        "search_content_id + suggested_fetch_source_ids to fetch selected search results."
        "URLs are retained internally for provenance and document routing. "
        "web_search has not fetched page bodies."
    )

    if output_notes:
        lines.append("notes:")
        lines.extend(f"- {note}" for note in output_notes)

    if ranked_payload:
        lines.append("ranked_search_evidence:")
        for i, item in enumerate(ranked_payload, 1):
            lines.extend(
                [
                    f"- rank: {i}",
                    f"  source_id: {item['source_id']}",
                    f"  title: {item['title']}",
                    f"  domain: {item['domain']}",
                    f"  urls: {item['urls']}",
                    f"  snippet: {item['snippet']}",
                    f"  lexical_score: {item['lexical_score']}",
                    f"  matched_reason: {item['matched_reason']}",
                ]
            )
            if item["term_hit_stats"]:
                lines.append(
                    f"  term_hit_stats: "
                    f"{json.dumps(item['term_hit_stats'], ensure_ascii=False)}"
                )
            lines.append("  suggested_next_action: web_fetch_by_source_id")
    else:
        lines.append("ranked_search_evidence: []")

    return lines


def _format_response(
    response: SearchResponse,
    *,
    mode: SearchMode,
    queries: List[str],
    notes: Optional[List[str]] = None,
    candidate_pages: Optional[List[CandidatePage]] = None,
    source_display_limit: int,
    groundings: List[WikipediaGroundingResult] = None,
) -> str:
    """格式化 FAST 模式下直接可见的搜索结果。"""
    unique_domains = count_unique_domains(response.results)
    output_notes = notes or []
    groundings_list = groundings or []

    display_results = response.results[:source_display_limit]
    if len(response.results) > source_display_limit:
        add_note(
            output_notes,
            f"Sources list was shortened to {source_display_limit} items because {mode.value} mode prioritizes concise evidence.",
        )

    lines = [f"[Tool Result] Web search evidence pack\nMode: {mode.value}"]

    if queries:
        lines.append("Queries:")
        lines.extend(f"- {q}" for q in queries)

    if response.source:
        lines.append(f"Source: {response.source}")

    lines.append(
        f"Summary: {len(response.results)} results, {unique_domains} unique domains."
    )
    lines.append(
        "Result order: reranked order after multi-query/provider fusion and deduplication."
    )
    lines.append(
        "Tool-use note: fast mode returns snippets only. Never call web_fetch after fast mode. "
        "normal/deep mode returns candidate evidence, not fetched page bodies. "
        "Use web_fetch with from_search_content_id plus source_ids only when page-body evidence is needed."
    )

    if output_notes:
        lines.append("Notes:")
        lines.extend(f"- {note}" for note in output_notes)

    if groundings_list:
        lines.extend(
            [
                "\nBackground Groundings:",
                "The following are Wikipedia search results, use them as background information. "
                "Wikipedia content provides reference for entity disambiguation and terminology context. "
                "It is not fetched page content, not a ranked evidence source, not a candidate page "
                "for web_fetch, and must not override current web search results.",
            ]
        )
        for gr in groundings_list:
            lines.append(f"- {gr.title} (keyword: {gr.keyword}")
            if gr.extract:
                lines.append(f"  {gr.extract}")
            if gr.url:
                lines.append(f"  Source: {gr.url}")

    if candidate_pages:
        candidate_ids = {item.source_id for item in candidate_pages}
        lines.extend(
            [
                "\nCandidate pages for web_fetch:",
                "These pages have NOT been fetched. Snippets are search-engine previews, not page content. "
                f"Up to {DEPTH_BUDGET_MAP[mode].candidate_page_limit} candidate source_ids if web_fetch is necessary"
                "If page-body evidence is needed, select source_ids and pass them to "
                "web_fetch with from_search_content_id. Candidate pages are marked with [C].",
            ]
        )
        lines.extend(f"  [C] [{sid}]" for sid in candidate_ids)
    else:
        candidate_ids = set()

    if display_results:
        lines.extend(
            [
                "\nSources (reranked order for citations):",
                "Evidence only. Source markers [1], [2], ... and citation metadata use this reranked order, "
                "not the original search-provider order.",
            ]
        )

    for i, result in enumerate(display_results, 1):
        sid = str(i)
        title = result.title.strip() or result.url or "(no title)"
        url = result.url.strip()
        candidate_marker = " [C]" if sid in candidate_ids else ""

        lines.extend(
            [
                f"\n[{i}]{candidate_marker} Title: {title}",
                f"   Domain: {extract_domain(url)}",
                f"   URL: {url}",
                f"   Snippet: {result.snippet.strip()}",
            ]
        )

    return "\n".join(lines).strip()


def _build_suggested_fetch_source_ids(
    ranked_payload: List[Dict[str, Any]],
    mode: SearchMode,
) -> List[str]:
    """构建当前流程。"""
    limit = DEPTH_BUDGET_MAP[mode].suggested_fetch_limit
    source_ids: List[str] = []
    seen: Set[str] = set()

    for item in ranked_payload:
        source_id = item.get("source_id")
        if source_id and source_id not in seen:
            seen.add(source_id)  # type: ignore
            source_ids.append(source_id)  # type: ignore
            if len(source_ids) >= limit:
                break

    return source_ids
