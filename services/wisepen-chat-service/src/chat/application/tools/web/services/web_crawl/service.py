from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional
from urllib.parse import unquote, urlparse

from chat.application.algorithms.hash import stable_hash
from chat.application.algorithms.ranking.fielded_bm25 import score_fielded_bm25, FieldedItem
from chat.application.algorithms.ranking.tokenizer import tokenize_for_bm25
from chat.application.security.url_security import (
    UrlSecurityError,
    validate_public_http_url,
)
from chat.application.tools.web.services.common.file_handoff.store import TemporaryFileHandoffStore
from chat.application.tools.web.services.web_crawl.enums import (
    CrawlItemKind,
    CrawlSkipReason,
)
from chat.application.tools.web.services.web_crawl.models import (
    CrawlRequest,
    CrawlResult,
    CrawlResultItem,
)
from chat.application.tools.web.services.web_crawl.runtime.crawl_frontier import CrawlFrontier
from chat.application.tools.web.services.web_crawl.runtime.link_extractor import LinkExtractor
from chat.application.tools.web.services.web_crawl.runtime.models import LinkCandidate, CrawlFrontierItem
from chat.application.tools.web.services.web_crawl.runtime.politeness import PerHostPoliteness
from chat.application.tools.web.services.web_crawl.runtime.robots_policy import RobotsPolicy
from chat.application.tools.web.services.web_fetch.coordinator import (
    FetchCoordinator,
)
from chat.application.tools.web.services.web_fetch.models import (
    FetchedDocument,
    FetchedLink,
    FetchResultItem,
)
from chat.application.tools.web.utils.domains import extract_domain
from chat.application.tools.web.utils.markdown import extract_markdown_title
from chat.application.tools.web.utils.urls import canonicalize_url

_BLOCKED_SCHEMES = {
    "mailto",
    "tel",
    "javascript",
    "data",
    "blob",
    "ftp",
    "chrome",
    "about",
    "file",
}

_BLOCKED_MEDIA_EXTENSIONS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".ico",
    ".mp4",
    ".mp3",
    ".wav",
    ".woff",
    ".woff2",
    ".ttf",
)

_BLOCKED_PATH_PARTS = (
    "/login",
    "/signin",
    "/signup",
    "/register",
    "/logout",
    "/account",
    "/user",
    "/profile",
    "/cart",
    "/checkout",
    "/payment",
    "/billing",
    "/admin",
)

_LINK_FIELD_WEIGHTS = {
    "anchor_text": 3.0,
    "surrounding_text": 2.0,
    "source_title": 1.0,
    "url_terms": 0.7,
}

_INTERNAL_ACCEPT_THRESHOLD = 0.30
_EXTERNAL_ACCEPT_THRESHOLD = 0.45
_FETCH_WAVE_LIMIT = 3


@dataclass(frozen=True, slots=True)
class PreFetchDecision:
    """预抓取决策结果，包含是否允许抓取及拒绝原因。"""
    allowed: bool
    item: CrawlFrontierItem
    skip_reason: Optional[CrawlSkipReason] = None
    error: Optional[str] = None

    def to_result_item(self) -> CrawlResultItem:
        """将拒绝决策转换为跳过条目。"""
        return CrawlResultItem(
            url=self.item.url,
            kind=CrawlItemKind.SKIPPED,
            depth=self.item.depth,
            success=False,
            source_url=self.item.source_url,
            error=self.error,
            skip_reason=self.skip_reason,
        )


@dataclass(frozen=True, slots=True)
class HandleFetchResult:
    """抓取结果处理结果，包含生成的条目及统计数据。"""
    items: List[CrawlResultItem]
    fetched_pages: int = 0
    documents_found: int = 0
    skipped_count: int = 0


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """链接发现结果，包含跳过的条目计数。"""
    items: List[CrawlResultItem]
    skipped_count: int = 0


@dataclass(frozen=True, slots=True)
class RankedLinkCandidate:
    """经过 BM25 排序后的链接候选，包含相关性分数和是否接受标记。"""
    candidate: LinkCandidate
    score: float
    accepted: bool
    reject_reason: Optional[CrawlSkipReason] = None


class WebCrawlService:
    """编排网页爬取、链接发现、抓取降级和文件交接的全流程。"""

    def __init__(
        self,
        *,
        fetch_coordinator: FetchCoordinator,
        file_handoff_store: TemporaryFileHandoffStore,
        robots_policy: Optional[RobotsPolicy] = None,
        politeness_min_interval_seconds: float = 1.0,
    ):
        """注入抓取协调器、文件交接存储、robots 策略及礼貌间隔参数。"""
        self._fetch_coordinator = fetch_coordinator
        self._file_handoff_store = file_handoff_store
        self._robots_policy = robots_policy
        self._politeness_min_interval_seconds = politeness_min_interval_seconds

    async def crawl(self, request: CrawlRequest) -> CrawlResult:
        """执行网页爬取流程：URL 规范化 -> Frontier 调度 -> 预检 -> 抓取 -> 链接发现。"""
        seed_urls = [canonicalize_url(url) for url in request.seed_urls]

        frontier = CrawlFrontier(
            seed_urls=seed_urls,
            max_pages=request.max_pages,
            max_depth=request.max_depth,
        )
        robots_policy = self._robots_policy or RobotsPolicy()
        politeness = PerHostPoliteness(
            min_interval_seconds=self._politeness_min_interval_seconds
        )

        items: List[CrawlResultItem] = []
        fetched_pages = 0
        documents_found = 0
        skipped_count = 0

        # 主循环：每次从 frontier 弹出一批 URL，经过预检、礼貌等待后执行抓取
        while frontier.has_pending() and not frontier.reached_page_budget():
            batch = frontier.pop_next_batch(limit=_FETCH_WAVE_LIMIT)
            if not batch:
                break

            # 并发执行预抓取检查（安全过滤 + robots 查询）
            allowed_items: List[CrawlFrontierItem] = []
            decisions = await asyncio.gather(
                *[
                    self._pre_fetch_check(
                        item=item,
                        robots_policy=robots_policy,
                        politeness=politeness,
                        is_seed_url=item.depth == 0,
                    )
                    for item in batch
                ]
            )
            for decision in decisions:
                if decision.allowed:
                    allowed_items.append(decision.item)
                else:
                    items.append(decision.to_result_item())
                    skipped_count += 1

            # 过滤已被标记为阻塞的 host
            wave: List[CrawlFrontierItem] = []
            for item in allowed_items:
                if politeness.is_blocked(item.url):
                    items.append(
                        CrawlResultItem(
                            url=item.url,
                            kind=CrawlItemKind.SKIPPED,
                            depth=item.depth,
                            success=False,
                            source_url=item.source_url,
                            error="host blocked due to prior 429/403/503",
                            skip_reason=CrawlSkipReason.RATE_LIMITED,
                        )
                    )
                    skipped_count += 1
                else:
                    wave.append(item)

            if not wave:
                continue

            # 按 host 礼貌等待后并发抓取
            await self._wait_politeness(wave, politeness)
            fetch_results = await self._fetch_coordinator.fetch_many(
                [item.url for item in wave]
            )

            # 处理每个抓取结果
            for frontier_item, fetch_item in zip(wave, fetch_results):
                self._mark_blocked_if_needed(fetch_item, politeness)
                handled = await self._handle_fetch_result(
                    request=request,
                    frontier=frontier,
                    frontier_item=frontier_item,
                    fetch_item=fetch_item,
                )
                items.extend(handled.items)
                fetched_pages += handled.fetched_pages
                documents_found += handled.documents_found
                skipped_count += handled.skipped_count

        return CrawlResult(
            objective=request.objective,
            seed_urls=seed_urls,
            items=items,
            fetched_pages=fetched_pages,
            documents_found=documents_found,
            skipped_count=skipped_count,
            max_depth=request.max_depth,
            max_pages=request.max_pages,
            crawl_budget_exhausted=frontier.reached_page_budget() and frontier.has_pending(),
        )

    async def _pre_fetch_check(
        self,
        *,
        item: CrawlFrontierItem,
        robots_policy: RobotsPolicy,
        politeness: PerHostPoliteness,
        is_seed_url: bool,
    ) -> PreFetchDecision:
        """对单个 URL 执行抓取前检查：硬过滤 -> 安全校验 -> host 阻塞 -> robots 查询。"""
        try:
            canonical = canonicalize_url(item.url)
            blocked_reason = _hard_filter_reason(canonical)
            if blocked_reason is not None:
                return PreFetchDecision(False, item, blocked_reason)
            validated = validate_public_http_url(canonical)
        except UrlSecurityError as e:
            return PreFetchDecision(
                False,
                item,
                CrawlSkipReason.URL_SECURITY_REJECTED,
                str(e),
            )

        if politeness.is_blocked(validated):
            return PreFetchDecision(
                False,
                item,
                CrawlSkipReason.RATE_LIMITED,
                "host blocked due to prior 429/403/503",
            )

        robots = await robots_policy.can_fetch(
            url=validated,
            is_seed_url=is_seed_url,
        )
        if not robots.allowed:
            return PreFetchDecision(
                False,
                item,
                CrawlSkipReason.ROBOTS_UNAVAILABLE
                if robots.unavailable
                else CrawlSkipReason.ROBOTS_DISALLOWED,
                robots.reason.value if robots.reason else None,
            )

        return PreFetchDecision(
            allowed=True,
            item=CrawlFrontierItem(
                url=validated,
                depth=item.depth,
                origin_host=item.origin_host,
                current_host=extract_domain(validated),
                source_url=item.source_url,
                anchor_text=item.anchor_text,
                surrounding_text=item.surrounding_text,
                score=item.score,
                is_external=item.is_external,
                external_depth=item.external_depth,
            ),
        )

    async def _wait_politeness(
        self,
        items: List[CrawlFrontierItem],
        politeness: PerHostPoliteness,
    ) -> None:
        """并发等待一批 URL 各自的 host 礼貌间隔。"""
        await asyncio.gather(*(politeness.wait_turn(item.url) for item in items))

    async def _handle_fetch_result(
        self,
        *,
        request: CrawlRequest,
        frontier: CrawlFrontier,
        frontier_item: CrawlFrontierItem,
        fetch_item: FetchResultItem,
    ) -> HandleFetchResult:
        """处理单个 URL 的抓取结果：失败记录、文档交接或页面缓存+链接发现。"""
        if not fetch_item.success:
            reason = _map_fetch_error_to_skip_reason(fetch_item.error)
            return HandleFetchResult(
                items=[
                    CrawlResultItem(
                        url=fetch_item.url,
                        kind=CrawlItemKind.ERROR,
                        depth=frontier_item.depth,
                        success=False,
                        source_url=frontier_item.source_url,
                        error=fetch_item.error,
                        skip_reason=reason,
                    )
                ],
                skipped_count=1,
            )

        # 文档类型结果：写入文件交接存储并返回 file_ref
        if fetch_item.document is not None:
            try:
                file_ref = self._extract_file_ref(
                    user_id=request.user_id,
                    session_id=request.session_id,
                    document=fetch_item.document,
                )
            except Exception as e:
                return HandleFetchResult(
                    items=[
                        CrawlResultItem(
                            url=fetch_item.url,
                            kind=CrawlItemKind.ERROR,
                            depth=frontier_item.depth,
                            success=False,
                            source_url=frontier_item.source_url,
                            error=f"document handoff failed: {e.__class__.__name__}",
                            skip_reason=CrawlSkipReason.FETCH_FAILED,
                        )
                    ],
                    skipped_count=1,
                )

            return HandleFetchResult(
                items=[
                    CrawlResultItem(
                        url=fetch_item.url,
                        kind=CrawlItemKind.DOCUMENT,
                        depth=frontier_item.depth,
                        success=True,
                        source_url=frontier_item.source_url,
                        file_ref=file_ref,
                    )
                ],
                documents_found=1,
            )

        # 页面类型结果：保留原始 Markdown，最终工具输出由统一切面缓存和窗口化
        markdown = fetch_item.content or ""

        result_item = CrawlResultItem(
            url=fetch_item.url,
            kind=CrawlItemKind.PAGE,
            depth=frontier_item.depth,
            success=True,
            source_url=frontier_item.source_url,
            content_block=markdown,
        )

        discovery = DiscoveryResult(items=[])
        if frontier_item.depth < request.max_depth:
            discovery = self._discover_next_links(
                request=request,
                frontier=frontier,
                source_item=frontier_item,
                source_url=fetch_item.url,
                markdown=markdown,
                links=fetch_item.links,
            )

        return HandleFetchResult(
            items=[result_item, *discovery.items],
            fetched_pages=1,
            skipped_count=discovery.skipped_count,
        )

    def _extract_file_ref(
        self,
        *,
        user_id: str,
        session_id: str,
        document: FetchedDocument,
    ) -> str:
        """将抓取到的文档写入文件交接存储，返回可引用的 file_ref。"""
        handoff = self._file_handoff_store.write_bytes(
            user_id=user_id,
            session_id=session_id,
            filename=document.filename,
            content=document.content,
            canonical_suffix=Path(document.filename).suffix,
            content_type=document.media_type,
        )
        return handoff.file_ref

    def _discover_next_links(
        self,
        *,
        request: CrawlRequest,
        frontier: CrawlFrontier,
        source_item: CrawlFrontierItem,
        source_url: str,
        markdown: str,
        links: Optional[List[FetchedLink]] = None,
    ) -> DiscoveryResult:
        """从已抓取的页面中提取链接，经硬过滤、安全校验和 BM25 排序后入队 frontier。"""
        extracted = LinkExtractor.merge(
            markdown=markdown,
            base_url=source_url,
            fetched_links=links,
        )
        source_title = extract_markdown_title(markdown)
        candidates: List[LinkCandidate] = []
        skipped_items: List[CrawlResultItem] = []
        skipped_count = 0

        for link in extracted:
            _url_text = link.url.strip().lower()
            # 跳过内部引用（缓存ID、文件引用）
            if _url_text.startswith("cnt_") or _url_text.startswith("file_ref:"):
                skipped_items.append(
                    _skipped_item(
                        url=link.url,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=CrawlSkipReason.NON_URL_REFERENCE,
                        error="internal reference is not a URL",
                    )
                )
                skipped_count += 1
                continue

            try:
                canonical = canonicalize_url(link.url, base_url=source_url)
            except Exception as e:
                skipped_items.append(
                    _skipped_item(
                        url=link.url,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=CrawlSkipReason.URL_SECURITY_REJECTED,
                        error=f"URL canonicalization failed: {e.__class__.__name__}",
                    )
                )
                skipped_count += 1
                continue

            blocked_reason = _hard_filter_reason(canonical)
            if blocked_reason is not None:
                skipped_items.append(
                    _skipped_item(
                        url=canonical,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=blocked_reason,
                    )
                )
                skipped_count += 1
                continue

            try:
                validated = validate_public_http_url(canonical)
            except UrlSecurityError as e:
                skipped_items.append(
                    _skipped_item(
                        url=canonical,
                        source_url=source_url,
                        depth=source_item.depth + 1,
                        reason=CrawlSkipReason.URL_SECURITY_REJECTED,
                        error=str(e),
                    )
                )
                skipped_count += 1
                continue

            current_host = extract_domain(validated)
            is_external = current_host != source_item.origin_host
            external_depth = (
                source_item.external_depth + 1
                if source_item.is_external or is_external
                else 0
            )
            candidates.append(
                LinkCandidate(
                    id=stable_hash(validated),
                    url=validated,
                    anchor_text=link.anchor_text,
                    surrounding_text=link.surrounding_text,
                    source_title=source_title,
                    source_url=source_url,
                    depth=source_item.depth + 1,
                    origin_host=source_item.origin_host,
                    current_host=current_host,
                    is_external=is_external,
                    external_depth=external_depth,
                )
            )

        # 使用 BM25 对候选链接进行相关性排序
        ranked = _rank_link_candidates(
            objective=request.objective,
            candidates=candidates,
        )
        for item in ranked:
            if not item.accepted:
                skipped_count += 1
                continue

            frontier_item = CrawlFrontierItem(
                url=item.candidate.url,
                depth=item.candidate.depth,
                origin_host=item.candidate.origin_host,
                current_host=item.candidate.current_host,
                source_url=item.candidate.source_url,
                anchor_text=item.candidate.anchor_text,
                surrounding_text=item.candidate.surrounding_text,
                score=item.score,
                is_external=item.candidate.is_external,
                external_depth=item.candidate.external_depth,
            )
            added, reason = frontier.add_candidate(frontier_item)
            if added or reason == CrawlSkipReason.DUPLICATE_URL:
                continue
            skipped_items.append(
                _skipped_item(
                    url=item.candidate.url,
                    source_url=source_url,
                    depth=item.candidate.depth,
                    reason=reason or CrawlSkipReason.FETCH_FAILED,
                )
            )
            skipped_count += 1

        return DiscoveryResult(items=skipped_items, skipped_count=skipped_count)

    def _mark_blocked_if_needed(
        self,
        fetch_item: FetchResultItem,
        politeness: PerHostPoliteness,
    ) -> None:
        """如果抓取结果包含 rate limit 类错误，将对应 host 标记为阻塞。"""
        if fetch_item.success:
            return
        error = (fetch_item.error or "").lower()
        if "429" in error or "403" in error or "503" in error or "rate limit" in error:
            politeness.mark_blocked(fetch_item.url)


def _rank_link_candidates(
    *,
    objective: str,
    candidates: List[LinkCandidate],
) -> List[RankedLinkCandidate]:
    """使用 BM25 对候选链接进行相关性评分和排序。

    评分流程：
    1. 基于锚文本、上下文、来源标题和 URL 分词计算 BM25 分数。
    2. 若 BM25 全部为 0，退化为词重叠评分。
    3. 分数归一化后按阈值过滤（站内 0.30，站外 0.45）。
    4. 若候选数 <= 3 且无命中，强制接受第一个站内链接。
    """
    if not candidates:
        return []

    documents = [
        FieldedItem(
            id=candidate.id,
            fields={
                "anchor_text": candidate.anchor_text,
                "surrounding_text": candidate.surrounding_text,
                "source_title": candidate.source_title,
                "url_terms": _url_terms(candidate.url),
            },
        )
        for candidate in candidates
    ]
    raw_scores = score_fielded_bm25(objective, documents, _LINK_FIELD_WEIGHTS)
    # BM25 全零时退化为词重叠评分
    if not any(score > 0 for score in raw_scores.values()):
        raw_scores = {
            candidate.id: _score_link_text_overlap(objective, candidate)
            for candidate in candidates
        }
    max_score = max(raw_scores.values()) if raw_scores else 0.0
    normalized_scores = (
        {key: value / max_score for key, value in raw_scores.items()}
        if max_score > 0
        else {key: 0.0 for key in raw_scores}
    )
    by_id = {candidate.id: candidate for candidate in candidates}

    ranked: List[RankedLinkCandidate] = []
    for candidate_id, score in sorted(
        normalized_scores.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        candidate = by_id[candidate_id]
        threshold = (
            _EXTERNAL_ACCEPT_THRESHOLD
            if candidate.is_external
            else _INTERNAL_ACCEPT_THRESHOLD
        )
        accepted = score >= threshold
        ranked.append(
            RankedLinkCandidate(
                candidate=candidate,
                score=score,
                accepted=accepted,
                reject_reason=None if accepted else CrawlSkipReason.LOW_RELEVANCE,
            )
        )

    # 候选数较少且全部被拒时，强制接受第一个站内链接
    if len(candidates) <= 3 and not any(item.accepted for item in ranked):
        for index, item in enumerate(ranked):
            if item.candidate.is_external:
                continue
            ranked[index] = RankedLinkCandidate(
                candidate=item.candidate,
                score=item.score,
                accepted=True,
                reject_reason=None,
            )
            break

    return ranked


def _score_link_text_overlap(objective: str, candidate: LinkCandidate) -> float:
    """计算目标文本与候选链接各字段的词重叠得分，作为 BM25 的退化方案。"""
    query_tokens = set(tokenize_for_bm25(objective))
    if not query_tokens:
        return 0.0

    fields = {
        "anchor_text": candidate.anchor_text,
        "surrounding_text": candidate.surrounding_text,
        "source_title": candidate.source_title,
        "url_terms": _url_terms(candidate.url),
    }
    score = 0.0
    for field_name, text in fields.items():
        field_tokens = set(tokenize_for_bm25(text))
        if not field_tokens:
            continue
        score += _LINK_FIELD_WEIGHTS[field_name] * len(query_tokens & field_tokens)
    return score / max(1, len(query_tokens))


def _url_terms(url: str) -> str:
    """将 URL 中的主机名和路径拆分为空格分隔的单词，用于 BM25 分词。"""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    path = unquote(parsed.path or "")
    text = f"{host} {path}"
    return (
        text.replace("/", " ")
        .replace("-", " ")
        .replace("_", " ")
        .replace(".", " ")
    )


def _hard_filter_reason(url: str) -> Optional[CrawlSkipReason]:
    """对 URL 执行硬过滤检查：不支持的协议、媒体后缀和敏感路径。"""
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme in _BLOCKED_SCHEMES or scheme not in {"http", "https"}:
        return CrawlSkipReason.UNSUPPORTED_SCHEME
    path = parsed.path.lower()
    if any(path == part or path.startswith(part + "/") for part in _BLOCKED_PATH_PARTS):
        return CrawlSkipReason.BLOCKED_PATH
    if path.endswith(_BLOCKED_MEDIA_EXTENSIONS):
        return CrawlSkipReason.UNSUPPORTED_MEDIA
    return None


def _map_fetch_error_to_skip_reason(error: Optional[str]) -> CrawlSkipReason:
    """将抓取错误信息映射到对应的跳过原因枚举值。"""
    text = (error or "").lower()
    if "captcha" in text:
        return CrawlSkipReason.CAPTCHA_DETECTED
    if "bot" in text or "challenge" in text:
        return CrawlSkipReason.BOT_CHALLENGE
    if "login" in text or "signin" in text:
        return CrawlSkipReason.LOGIN_REQUIRED
    if "paywall" in text:
        return CrawlSkipReason.PAYWALL_DETECTED
    if "permission" in text or "403" in text:
        return CrawlSkipReason.PERMISSION_DENIED
    if "429" in text or "rate limit" in text:
        return CrawlSkipReason.RATE_LIMITED
    if "javascript" in text or "js required" in text:
        return CrawlSkipReason.JS_REQUIRED
    if "spa" in text:
        return CrawlSkipReason.SPA_SHELL
    return CrawlSkipReason.FETCH_FAILED


def _skipped_item(
    *,
    url: str,
    source_url: str,
    depth: int,
    reason: CrawlSkipReason,
    error: Optional[str] = None,
) -> CrawlResultItem:
    """快速构造一个跳过条目。"""
    return CrawlResultItem(
        url=url,
        kind=CrawlItemKind.SKIPPED,
        depth=depth,
        success=False,
        source_url=source_url,
        error=error,
        skip_reason=reason,
    )


