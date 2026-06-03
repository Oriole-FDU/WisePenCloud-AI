import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Union

from chat.application.security.url_security import (
    UrlSecurityError,
    validate_public_http_url,
)
from chat.application.tools.common.evidence_ranking.models import RankedEvidence
from chat.application.tools.common.evidence_ranking.ranking import rank_evidence
from chat.application.tools.tool_content_store import ToolContentStore
from chat.application.tools.web.services.common.file_handoff.store import TemporaryFileHandoffStore
from chat.application.tools.web.services.web_fetch import FetchCoordinator
from chat.application.tools.web.services.web_fetch.enums import FetcherName
from chat.application.tools.web.services.web_fetch.models import (
    FetchedDocument,
    FetchResultItem,
)
from chat.application.tools.web.utils.domains import extract_domain
from chat.application.tools.web.utils.markdown import extract_markdown_title
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

# 规则与硬限值常量
_PAGE_EVIDENCE_LIMIT_PER_PAGE = 3
MAX_FETCH_URLS = 20
_URL_PATTERN = re.compile(r"https?://[^\s<>'\"\]\[]+")
_TRAILING_URL_PUNCTUATION = ".,;:!?，。；：！？"

_TOOL_DESCRIPTION = (
    "Fetches page-body content or document handoffs from one or more web sources.\n\n"
    "Use this tool to read direct user-provided URLs, URLs supplied by another tool, "
    "or selected sources from a prior web_search evidence pack.\n\n"
    "web_fetch has two input modes: direct URL mode via urls, and web_search source "
    "mode via from_search_content_id plus source_ids. Use exactly one mode per call, "
    "and batch multiple URLs or source_ids in one call.\n\n"
    "HTML and text pages return readable Markdown in the raw tool result. When "
    "objective is provided, web_fetch also returns objective-relevant evidence snippets "
    "extracted from the fetched Markdown.\n\n"
    "Large web_fetch outputs may be cached by the runtime as ToolContent. If the "
    "runtime returns ToolContent metadata, use evidence_rank to locate relevant "
    "passages or tool_content_read to continue a known window by next_offset.\n\n"
    "Direct document links such as PDF, DOCX, PPTX, EPUB, XLSX, XLSM, XLS, or ODS "
    "are returned as file_ref handoffs instead of being parsed by web_fetch.\n"
    "After web_fetch returns file_ref values, pass all file_refs together to document_parse in one call."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "urls": {
            "type": "array",
            "items": {
                "type": "string",
                "minLength": 1,
                "pattern": "^https?://",
            },
            "minItems": 1,
            "maxItems": MAX_FETCH_URLS,
            "uniqueItems": True,
            "description": (
                "Direct URL mode. One batch of http:// or https:// URLs to fetch. "
                "Use this for user-provided URLs, non-search URLs, fetcher debugging, "
                "or URLs owned by another tool. Do not use this for URLs selected "
                "from web_search results."
            ),
        },
        "from_search_content_id": {
            "type": "string",
            "description": (
                "web_search source mode. The content_id of a prior web_search evidence "
                "pack. Required with source_ids when fetching selected web_search results."
            ),
        },
        "source_ids": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "maxItems": MAX_FETCH_URLS,
            "uniqueItems": True,
            "description": (
                "web_search source mode. Source IDs selected from the prior web_search "
                "evidence pack identified by from_search_content_id. Do not manually "
                "copy URLs from web_search into urls."
            ),
        },
        "objective": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional evidence extraction goal. When provided, web_fetch returns "
                "page evidence relevant to this goal from the fetched Markdown."
            ),
        },
    },
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class FetchSourceContext:
    """来自先前搜索凭据包的上下文追溯模型"""
    from_search_content_id: str
    source_id: str
    title: Optional[str] = None
    url: Optional[str] = None
    domain: Optional[str] = None


@dataclass(frozen=True, slots=True)
class FetchTarget:
    """封装待抓取的目标 URL 及其归属上下文"""
    url: str
    source_context: Optional[FetchSourceContext] = None


class WebFetchTool(BaseTool):
    """网页/文档内容批量抓取并中转至系统上下文的工具实现。"""

    def __init__(
            self,
            fetcher: FetchCoordinator,
            file_handoff_store: TemporaryFileHandoffStore,
            content_store: ToolContentStore,
    ):
        """初始化对象依赖。"""
        self._fetcher = fetcher
        self._file_handoff_store = file_handoff_store
        self._content_store = content_store

    @property
    def name(self) -> str:
        """返回组件名称。"""
        return "web_fetch"

    @property
    def description(self) -> str:
        """返回组件说明。"""
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        """返回工具参数结构。"""
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """解析抓取目标并调用降级协调链执行批量抓取，支持单 URL 模式与搜索包跟踪模式。"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        targets_or_error = _resolve_fetch_targets(
            session_id=session_id,
            kwargs=kwargs,
            content_store=self._content_store,
        )
        if isinstance(targets_or_error, str):
            return targets_or_error

        targets, warnings = targets_or_error
        if not targets and not warnings:
            return (
                "[Tool Error] Missing required input: pass urls, or pass "
                "from_search_content_id with source_ids."
            )

        urls = [target.url for target in targets]

        results = await self._fetcher.fetch_many(urls)

        raw_objective: Optional[str] = kwargs.get("objective")
        return self._format_batch_result(
            user_id=user_id,
            session_id=session_id,
            results=results,
            warnings=warnings,
            target_contexts=[target.source_context for target in targets],
            objective=raw_objective.strip() if raw_objective else None,
        )

    def _format_batch_result(
            self,
            *,
            user_id: str,
            session_id: str,
            results: List[FetchResultItem],
            warnings: List[str],
            target_contexts: List[Optional[FetchSourceContext]],
            objective: Optional[str],
    ) -> str:
        """合并并格式化当前批次的所有抓取成功、中转文件或重定向跳转的结果输出。"""
        lines: List[str] = ["[Tool Result] web_fetch 批量结果"]

        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count
        lines.append(
            f"Total: {len(results)} URLs，{success_count} 个已完成，{fail_count} 个未完成。\n"
        )

        if warnings:
            lines.append("")
            lines.append(f"{len(warnings)} 个 URL 被安全策略拦截。")
            lines.append("Warnings:")
            for warning in warnings:
                lines.append(f"- {warning.strip()}")

        for item, source_context in zip(results, target_contexts):
            if item.success:
                # 针对静态流识别出的二进制文档，执行文件交接存储逻辑。
                if item.document is not None:
                    lines.append("")
                    lines.extend(
                        self._format_document_handoff_lines(
                            user_id=user_id,
                            session_id=session_id,
                            document=item.document,
                        )
                    )
                elif item.content is not None:
                    lines.append("")
                    lines.append(f"--- URL: {item.url} ---")
                    provenance = _build_page_metadata(
                        item=item,
                        source_context=source_context,
                        objective=objective,
                    )
                    for key in (
                            "source_id",
                            "from_search_content_id",
                            "title",
                            "domain",
                            "final_url",
                            "status_code",
                            "fetcher",
                            "cache_hit",
                    ):
                        value = provenance.get(key)
                        if value is None:
                            continue
                        if isinstance(value, bool):
                            value = str(value).lower()
                        lines.append(f"{key}: {value}")

                    page_content_id: Optional[str] = None
                    page_cache_error: Optional[str] = None

                    if objective:
                        page_receipt = self._content_store.put_receipt(
                            session_id=session_id,
                            tool_name=self.name,
                            source=item.url,
                            text=item.content,
                            content_type="text/markdown",
                            metadata=provenance,
                        )
                        if page_receipt is not None and page_receipt.cached:
                            page_content_id = page_receipt.content_id
                        else:
                            page_cache_error = (
                                page_receipt.cache_error
                                if page_receipt is not None
                                else "failed_to_cache_page_content"
                            )

                        lines.extend(
                            _format_page_evidence_lines(
                                session_id=session_id,
                                raw_content_id=page_content_id,
                                cache_error=page_cache_error,
                                objective=objective,
                                content_store=self._content_store,
                            )
                        )

                    lines.extend(
                        [
                            "",
                            "[Fetched Markdown]",
                            item.content,
                        ]
                    )
            else:
                lines.append("")
                lines.append(f"--- URL: {item.url} ---")
                if source_context is not None:
                    lines.append(f"source_id: {source_context.source_id}")
                    lines.append(
                        f"from_search_content_id: {source_context.from_search_content_id}"
                    )
                # 处理未跟随的跨站重定向引导
                if item.redirect_url:
                    lines.extend(
                        [
                            "[Redirect]",
                            f"redirect_url: {item.redirect_url}",
                        ]
                    )
                    if item.status_code is not None:
                        lines.append(f"status_code: {item.status_code}")
                    lines.extend(
                        [
                            "redirect_policy: same-host redirects are followed automatically; cross-host redirects require an explicit follow-up fetch.",
                            "next_step: decide whether the redirect target is relevant, then call web_fetch with redirect_url if needed."
                        ]
                    )
                    if item.fetcher:
                        lines.append(f"fetcher: {item.fetcher.value}")
                    if item.error:
                        lines.append(f"note: {item.error}")
                    continue
                lines.append(f"[Fetch Error] {item.error}")

        return "\n".join(lines)

    def _format_document_handoff_lines(
            self,
            *,
            user_id: str,
            session_id: str,
            document: FetchedDocument,
    ) -> List[str]:
        """将获取到的原始文档存入暂存区，并返回生成供后续 document_parse 消费的 file_ref 凭证。"""
        handoff = self._file_handoff_store.write_bytes(
            user_id=user_id,
            session_id=session_id,
            filename=document.filename,
            content=document.content,
            canonical_suffix=Path(document.filename).suffix,
            content_type=document.media_type,
        )
        file_ref = handoff.file_ref

        return [
            f"--- URL: {document.url} ---",
            "Downloaded a document file. Web Fetch does not parse document content.",
            f"file_ref: {file_ref}",
            f"source_url: {document.url}",
            f"filename: {document.filename}",
            f"content_type: {document.media_type}",
            f"size_bytes: {len(document.content)}",
            "next_step: Collect every file_ref from this web_fetch batch, inject all of them into one document_parse file_refs list, and call document_parse once: document_parse(file_refs=[file_ref_1, file_ref_2, ...]).",
        ]


def _resolve_fetch_targets(
        *,
        session_id: str,
        kwargs: Dict[str, Any],
        content_store: ToolContentStore,
):
    """两路输入路由统一解析器。"""
    has_urls = "urls" in kwargs
    has_from_search_content_id = "from_search_content_id" in kwargs
    has_source_ids = "source_ids" in kwargs
    has_search_mode = has_from_search_content_id or has_source_ids

    if has_urls and has_search_mode:
        return (
            "[Tool Error] web_fetch accepts exactly one input mode: pass urls, "
            "or pass from_search_content_id with source_ids."
        )

    if not has_urls and not has_search_mode:
        return (
            "[Tool Error] Missing required input: pass urls, or pass "
            "from_search_content_id with source_ids."
        )

    if has_urls:
        urls = _normalize_fetch_urls(kwargs["urls"])
        if not urls:
            return "[Tool Error] urls contains no valid http:// or https:// URL."

        targets: List[FetchTarget] = []
        warnings: List[str] = []
        for url in urls:
            try:
                safe_url = validate_public_http_url(url)
            except UrlSecurityError as e:
                warnings.append(f"[Security Warning] URL {url} rejected by security policy: {e}")
                continue

            targets.append(FetchTarget(url=safe_url))

        return targets, warnings

    if not has_from_search_content_id or not has_source_ids:
        return (
            "[Tool Error] web_search source mode requires both "
            "from_search_content_id and source_ids."
        )

    resolved_or_error = _resolve_source_id_targets(
        session_id=session_id,
        from_search_content_id=kwargs["from_search_content_id"],
        source_ids=kwargs["source_ids"],
        content_store=content_store,
    )
    if isinstance(resolved_or_error, str):
        return resolved_or_error

    raw_targets, warnings = resolved_or_error
    targets: List[FetchTarget] = []
    seen_urls: Set[str] = set()
    for target in raw_targets:
        if target.url in seen_urls:
            continue
        seen_urls.add(target.url)
        targets.append(target)

    return targets, warnings


def _resolve_source_id_targets(
        *,
        session_id: str,
        from_search_content_id: str,
        source_ids: List[str],
        content_store: ToolContentStore,
):
    """从内容存贮区中读取先前的搜索凭据包，依 source_ids 检索恢复原始 URL。"""
    stored = content_store.get(
        content_id=from_search_content_id,
        session_id=session_id,
    )
    if stored is None:
        return (
            "[Tool Error] from_search_content_id not found, expired, or inaccessible."
        )

    try:
        payload = json.loads(stored.text)
    except json.JSONDecodeError:
        return "[Tool Error] from_search_content_id does not contain valid JSON."

    content_kind = payload.get("content_kind")
    if content_kind != "web_search_evidence_pack":
        return (
            "[Tool Error] from_search_content_id must reference a web_search evidence pack."
        )

    raw_results = payload.get("results")
    results_by_source_id: Dict[str, Dict[str, Any]] = {
        item["source_id"]: item
        for item in raw_results
        if item.get("source_id")
    }

    missing = [
        source_id for source_id in source_ids
        if source_id not in results_by_source_id
    ]
    if missing:
        return (
                "[Tool Error] source_ids not found in from_search_content_id: "
                + ", ".join(missing)
                + "."
        )

    targets: List[FetchTarget] = []
    warnings: List[str] = []
    for source_id in source_ids:
        item = results_by_source_id[source_id]
        raw_url = item.get("urls")
        if not isinstance(raw_url, str) or not raw_url.strip():
            return f"[Tool Error] source_id {source_id} has no URL."

        urls = _normalize_fetch_urls([raw_url])
        if not urls:
            return f"[Tool Error] source_id {source_id} has no URL."

        try:
            url = validate_public_http_url(urls[0])
        except UrlSecurityError as e:
            warnings.append(f"[Tool Error] source_id {source_id} URL rejected by security policy: {e}")
            continue

        title = item.get("title")
        domain = item.get("domain")
        targets.append(
            FetchTarget(
                url=url,
                source_context=FetchSourceContext(
                    from_search_content_id=from_search_content_id,
                    source_id=source_id,
                    title=title if isinstance(title, str) else None,
                    url=url,
                    domain=domain if isinstance(domain, str) else extract_domain(url),
                ),
            )
        )

    return targets, warnings


def _normalize_fetch_urls(raw_urls: List[str]) -> List[str]:
    """清洗提取字符串中的标准公开 HTTP(S) 链接并去重。"""
    urls: List[str] = []
    seen: Set[str] = set()

    for item in raw_urls:
        matches = _URL_PATTERN.findall(item)
        for candidate in matches or [item]:
            url = candidate.strip().rstrip(_TRAILING_URL_PUNCTUATION)
            if url and url not in seen:
                seen.add(url)
                urls.append(url)

    return urls


def _build_page_metadata(
        *,
        item: FetchResultItem,
        source_context: Optional[FetchSourceContext],
        objective: Optional[str],
) -> Dict[str, Any]:
    """打包缓存器所需的小型元数据字典契约。"""
    final_url = item.final_url or item.url
    title = item.title or (source_context.title if source_context else None)
    if not title and item.content is not None:
        title = extract_markdown_title(item.content)

    domain = (
            item.domain
            or extract_domain(final_url)
            or (source_context.domain if source_context else None)
    )

    metadata: Dict[str, Any] = {
        "content_kind": "web_page",
        "urls": item.url,
        "final_url": final_url,
        "status_code": item.status_code,
        "cache_hit": item.fetcher is FetcherName.CACHE,
    }
    if title:
        metadata["title"] = title
    if domain:
        metadata["domain"] = domain
    if item.fetcher:
        metadata["fetcher"] = item.fetcher.value
    if source_context:
        metadata["source_id"] = source_context.source_id
        metadata["from_search_content_id"] = source_context.from_search_content_id
    if objective:
        metadata["objective"] = objective

    return metadata


def _format_page_evidence_lines(
    *,
    session_id: str,
    raw_content_id: Optional[str],
    cache_error: Optional[str],
    objective: str,
    content_store: ToolContentStore,
) -> List[str]:
    """通过词法分块机制提取出网页中与目标意图最具匹配度的线索片段。"""
    lines = [
        "",
        "[Page Evidence]",
        f"raw_content_id: {raw_content_id or ''}",
        f"objective: {objective}",
        "extraction_method: lexical_chunk_ranking",
        "final_answer_generated: false",
        "note: Evidence snippets are extracted from fetched raw Markdown; web_fetch does not answer the user directly.",
    ]

    if not raw_content_id:
        lines.extend(
            [
                "needs_more_context: true",
                "extracted_evidence: []",
                "notes:",
                f"- Raw page content was not cached: {cache_error or 'unknown cache error'}.",
            ]
        )
        return lines

    try:
        ranking_result = rank_evidence(
            query=objective,
            content_ids=[raw_content_id],
            session_id=session_id,
            max_evidence=_PAGE_EVIDENCE_LIMIT_PER_PAGE,
            content_store=content_store,
        )
    except Exception as e:
        log_fail(
            "web_fetch page evidence extraction",
            repr(e),
            session_id=session_id,
            content_id=raw_content_id,
        )
        lines.extend(
            [
                "needs_more_context: true",
                "extracted_evidence: []",
                "notes:",
                "- Page evidence extraction failed; raw Markdown remains cached.",
            ]
        )
        return lines

    evidence_items = [
        evidence
        for evidence in ranking_result.evidence
        if evidence.content_id == raw_content_id and evidence.chunk_index >= 0
    ]
    needs_more_context = not evidence_items
    lines.append(f"needs_more_context: {str(needs_more_context).lower()}")

    if not evidence_items:
        lines.append("extracted_evidence: []")
    else:
        lines.append("extracted_evidence:")
        for index, evidence in enumerate(evidence_items, 1):
            lines.extend(_format_page_evidence_item(index=index, evidence=evidence))

    if ranking_result.notes:
        lines.append("notes:")
        for note in ranking_result.notes:
            lines.append(f"- {note}")

    return lines


def _format_page_evidence_item(
        *,
        index: int,
        evidence: RankedEvidence,
) -> List[str]:
    """单条核心片段元数据及摘要的高可读行输出格式化。"""
    lines = [
        f"- rank: {index}",
        f"  raw_content_id: {evidence.content_id}",
        f"  chunk_index: {evidence.chunk_index}",
        f"  title: {evidence.display_title}",
    ]
    if evidence.url:
        lines.append(f"  urls: {evidence.url}")
    if evidence.domain:
        lines.append(f"  domain: {evidence.domain}")

    lines.extend(
        [
            f"  start_offset: {evidence.start_offset}",
            f"  end_offset: {evidence.end_offset}",
            f"  lexical_score: {evidence.score:.4f}",
            f"  matched_reason: {evidence.matched_reason}",
        ]
    )

    if evidence.term_hit_stats:
        term_hit_stats = []
        for stat in evidence.term_hit_stats:
            term_hit_stats.append(
                {
                    "term": stat.term,
                    "total_count": stat.total_count,
                    "field_stats": [
                        {"field": field_stat.field, "count": field_stat.count}
                        for field_stat in stat.field_stats
                    ],
                }
            )
        lines.append(
            "  term_hit_stats: " + json.dumps(term_hit_stats, ensure_ascii=False)
        )

    if evidence.excerpt:
        lines.append("  excerpt: |-")
        for excerpt_line in evidence.excerpt.splitlines():
            lines.append(f"    {excerpt_line}")

    lines.append("  suggested_next_action: use_page_evidence_or_read_more_context")
    return lines