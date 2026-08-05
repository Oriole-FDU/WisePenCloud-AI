from __future__ import annotations

import asyncio

import regex

from chat.application.tools.common.canonical_token_budget import (
    count_canonical_tokens,
    truncate_canonical_prefix,
    truncate_canonical_suffix,
)
from chat.application.tools.common.tool_content_store import (
    StoredToolContent,
    ToolContentChunk,
    ToolContentStore,
)
from chat.application.utils.chunkers import LocatorKind, TextLocator
from chat.application.utils.ranking import RankCandidate, RankQuery, RankRequest
from chat.application.utils.ranking.pipeline import RankingPipeline

from .content_window_builder import ToolContentWindowBuilder, chunk_text
from .models import (
    ToolContentPageReadItem,
    ToolContentPageReadResult,
    ToolContentReadFailure,
    ToolContentRangeReadResult,
    ToolContentRegexSearchMatch,
    ToolContentRegexSearchRequest,
    ToolContentRegexSearchResult,
    ToolContentSectionReadItem,
    ToolContentSectionReadResult,
    ToolContentSemanticSearchItem,
    ToolContentSemanticSearchRequest,
    ToolContentSemanticSearchResult,
    ToolContentSnapshotAnchor,
    ToolContentSnapshotPage,
    ToolContentSnapshotResult,
    ToolContentSnapshotSection,
)

_SEARCH_TIMEOUT_SECONDS = 5


class ToolContentInvalidRegexError(ValueError):
    """正则表达式语法无效，搜索尚未开始。"""


class ToolContentRegexTimeoutError(TimeoutError):
    """单次正则搜索超过执行时间限制。"""


class ToolContentService:
    """按结构快照、search 和 read 三类语义访问权威工具原文。

    service 只从 `ToolContentStore` 读取权威原文和解析结果，再把它们转换为
    模型可见的窗口。字符 offset 用于定位原文，canonical token budget 用于
    控制模型输出；两者由 window builder 协同维护，但不能互相替代。

    各类读取共享“单次请求总预算”，而不是共享某个窗口的预算：
    page/section 会按请求顺序消费总预算，regex 会按命中顺序消费总预算，
    semantic search 会按 ranking 顺序展开结果。窗口级上限则由对应 builder
    独立执行，防止单个结果吞掉整个响应。
    """

    __slots__ = (
        "_read_total_token_budget",
        "_read_window_builder",
        "_regex_context_side_token_budget",
        "_regex_total_token_budget",
        "_ranking_pipeline",
        "_semantic_search_total_token_budget",
        "_semantic_search_window_builder",
        "_store",
    )

    def __init__(
        self,
        *,
        read_window_token_budget: int,
        read_total_token_budget: int,
        semantic_search_window_token_budget: int,
        semantic_search_total_token_budget: int,
        regex_context_side_token_budget: int,
        regex_total_token_budget: int,
        ranking_pipeline: RankingPipeline,
        store: ToolContentStore,
    ) -> None:
        """初始化内容读取策略、ranking pipeline 和权威内容存储。

        预算在这里集中校验，之后内部方法可以假定所有预算都是正数；这样
        避免在每个读取阶段重复做同一项配置校验。
        """

        if min(
            read_window_token_budget,
            read_total_token_budget,
            semantic_search_window_token_budget,
            semantic_search_total_token_budget,
            regex_context_side_token_budget,
            regex_total_token_budget,
        ) < 1:
            raise ValueError("tool content token budgets must be greater than 0")
        self._ranking_pipeline = ranking_pipeline
        self._store = store
        self._read_window_builder = ToolContentWindowBuilder(
            token_budget=read_window_token_budget
        )
        self._semantic_search_window_builder = ToolContentWindowBuilder(
            token_budget=semantic_search_window_token_budget
        )
        self._read_total_token_budget = read_total_token_budget
        self._semantic_search_total_token_budget = semantic_search_total_token_budget
        self._regex_context_side_token_budget = regex_context_side_token_budget
        self._regex_total_token_budget = regex_total_token_budget

    async def read_range(
        self,
        *,
        content_id: str,
        session_id: str,
        start: int | None,
        end: int | None,
    ) -> ToolContentRangeReadResult:
        """按 Python 字符范围读取一个连续窗口。"""

        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentRangeReadResult(
                content_id=content_id,
                reason="content_not_found",
            )
        return ToolContentRangeReadResult(
            content_id=content_id,
            window=self._read_window_builder.build_range_window(
                stored,
                start=start,
                end=end,
            ),
        )

    async def read_pages(
        self,
        *,
        content_id: str,
        session_id: str,
        page_labels: tuple[str, ...],
    ) -> ToolContentPageReadResult:
        """按 page 标签读取多个 page，并返回每个 page 的独立状态。

        输入先去重但保持首次出现顺序。总预算在所有 page 之间共享；一个
        page 可以返回部分窗口并标记预算耗尽，后续 page 则只返回原因而不再
        读取正文。
        """

        unique_page_labels = tuple(dict.fromkeys(page_labels))
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentPageReadResult(
                content_id=content_id,
                items=tuple(
                    ToolContentPageReadItem(
                        page_label=page_label,
                        reason="content_not_found",
                    )
                    for page_label in unique_page_labels
                ),
            )

        # 存储层仍以 TextLocator 保存 page 的字符范围；这里转换成按业务
        # page label 索引的结构，避免在消费循环中反复扫描全部 locator。
        pages_by_label: dict[str, list[TextLocator]] = {}
        for text_range in stored.locators:
            if text_range.kind is LocatorKind.PAGE:
                pages_by_label.setdefault(
                    text_range.name.removeprefix("page:"),
                    [],
                ).append(text_range)

        items: list[ToolContentPageReadItem] = []
        remaining = self._read_total_token_budget
        budget_exhausted = False
        for page_label in unique_page_labels:
            if remaining <= 0:
                # 总预算耗尽后仍保留请求项，便于模型知道哪些 page 未读取，
                # 而不是把它们误判成不存在。
                budget_exhausted = True
                items.append(
                    ToolContentPageReadItem(
                        page_label=page_label,
                        reason="page_budget_exhausted",
                    )
                )
                continue

            page_ranges = tuple(pages_by_label.get(page_label, ()))
            if not page_ranges:
                items.append(
                    ToolContentPageReadItem(
                        page_label=page_label,
                        reason="page_not_found",
                    )
                )
                continue

            windows = []
            reason = None
            for page_range in page_ranges:
                if remaining <= 0:
                    # 一个 page 可能对应多个范围；前面的范围已经消耗完预算时，
                    # 当前 page 仍返回已读窗口，并在 item 上说明未完整展开。
                    budget_exhausted = True
                    reason = "page_budget_exhausted"
                    break
                window = self._read_window_builder.build_range_window(
                    stored,
                    start=page_range.start_offset,
                    end=page_range.end_offset,
                    token_budget=remaining,
                )
                windows.append(window)
                remaining -= count_canonical_tokens(window.text)
                if window.truncated:
                    # builder 已保留预算内的部分内容。这里不能丢弃它，只需
                    # 标记 item 和 result，通知调用方当前 page 不是完整正文。
                    budget_exhausted = True
                    reason = "page_budget_exhausted"
                    break
            items.append(
                ToolContentPageReadItem(
                    page_label=page_label,
                    windows=tuple(windows),
                    reason=reason,
                )
            )
        return ToolContentPageReadResult(
            content_id=stored.content_id,
            items=tuple(items),
            budget_exhausted=budget_exhausted,
        )

    async def read_sections(
        self,
        *,
        content_id: str,
        session_id: str,
        section_paths: tuple[str, ...],
    ) -> ToolContentSectionReadResult:
        """按 section path 读取多个 section，并返回每个 section 的独立状态。

        section path 是 snapshot 暴露的结构语义，不是存储层 locator 名称。
        和 page 读取一样，所有请求共享一个总 token 预算，并保留预算内的
        部分窗口。
        """

        unique_section_paths = tuple(dict.fromkeys(section_paths))
        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentSectionReadResult(
                content_id=content_id,
                items=tuple(
                    ToolContentSectionReadItem(
                        section_path=section_path,
                        reason="content_not_found",
                    )
                    for section_path in unique_section_paths
                ),
            )

        # locator 只在存储边界出现；进入业务循环后按去掉内部前缀的 section
        # path 索引，避免把 locator 命名暴露成工具契约。
        sections_by_path: dict[str, list[TextLocator]] = {}
        for text_range in stored.locators:
            if text_range.kind is LocatorKind.SECTION:
                sections_by_path.setdefault(
                    text_range.name.removeprefix("section:"),
                    [],
                ).append(text_range)

        items: list[ToolContentSectionReadItem] = []
        remaining = self._read_total_token_budget
        budget_exhausted = False
        for section_path in unique_section_paths:
            if remaining <= 0:
                # 这里返回 section_budget_exhausted 而不是 section_not_found，
                # 因为 section 已经在 snapshot 中存在，只是本次没有预算读取。
                budget_exhausted = True
                items.append(
                    ToolContentSectionReadItem(
                        section_path=section_path,
                        reason="section_budget_exhausted",
                    )
                )
                continue

            section_ranges = tuple(sections_by_path.get(section_path, ()))
            if not section_ranges:
                items.append(
                    ToolContentSectionReadItem(
                        section_path=section_path,
                        reason="section_not_found",
                    )
                )
                continue

            windows = []
            reason = None
            for section_range in section_ranges:
                if remaining <= 0:
                    # section 可能由多个存储范围组成；已读范围仍然有效，后续
                    # 范围因共享总预算不足而停止。
                    budget_exhausted = True
                    reason = "section_budget_exhausted"
                    break
                window = self._read_window_builder.build_range_window(
                    stored,
                    start=section_range.start_offset,
                    end=section_range.end_offset,
                    token_budget=remaining,
                )
                windows.append(window)
                remaining -= count_canonical_tokens(window.text)
                if window.truncated:
                    # 保留 builder 返回的部分窗口，并显式传播截断原因。
                    budget_exhausted = True
                    reason = "section_budget_exhausted"
                    break
            items.append(
                ToolContentSectionReadItem(
                    section_path=section_path,
                    windows=tuple(windows),
                    reason=reason,
                )
            )
        return ToolContentSectionReadResult(
            content_id=stored.content_id,
            items=tuple(items),
            budget_exhausted=budget_exhausted,
        )

    async def get_snapshot(
        self,
        *,
        content_id: str,
        session_id: str,
    ) -> ToolContentSnapshotResult:
        """返回 page、section、anchor 结构和全文长度，不读取正文。"""

        stored = await self._store.get(content_id=content_id, session_id=session_id)
        if stored is None:
            return ToolContentSnapshotResult(
                content_id=content_id,
                reason="content_not_found",
            )
        return ToolContentSnapshotResult(
            content_id=content_id,
            content_type=stored.content_type,
            total_length=len(stored.text),
            pages=_snapshot_pages(stored.locators),
            sections=_snapshot_sections(stored.locators),
            anchors=_snapshot_anchors(stored.locators),
            metadata=dict(stored.metadata),
        )

    async def regex_search(
        self,
        *,
        request: ToolContentRegexSearchRequest,
        session_id: str,
    ) -> ToolContentRegexSearchResult:
        """在多个工具内容中执行 regex 搜索并返回带上下文的窗口。

        regex 匹配本身发生在完整原文上，命中位置因此保持原始字符 offset；
        只有命中周围的上下文窗口受到 token 预算限制。搜索在工作线程执行，
        以便 regex 库的超时保护不会阻塞事件循环。
        """

        stored_items, failed = await self._load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )

        def scan_loaded() -> tuple[tuple[ToolContentRegexSearchMatch, ...], bool]:
            try:
                compiled = regex.compile(request.pattern)
            except regex.error as exc:
                raise ToolContentInvalidRegexError(str(exc)) from exc

            max_matches = max(request.max_matches, 0)
            matches: list[ToolContentRegexSearchMatch] = []
            remaining = self._regex_total_token_budget
            for content_id, stored in stored_items:
                try:
                    for matched in compiled.finditer(
                        stored.text,
                        timeout=_SEARCH_TIMEOUT_SECONDS,
                    ):
                        if remaining <= 0:
                            # 匹配扫描仍可能有结果，但模型窗口总预算已用尽；
                            # 返回已构建的结果并通过标记区分“没有更多命中”。
                            return tuple(matches), True
                        window_start, window_end = _regex_window_range(
                            stored.text,
                            match_start=matched.start(),
                            match_end=matched.end(),
                            context_chars=request.context_chars,
                            context_side_token_budget=self._regex_context_side_token_budget,
                            total_token_budget=remaining,
                        )
                        window = self._read_window_builder.build_range_window(
                            stored,
                            start=window_start,
                            end=window_end,
                            token_budget=remaining,
                        )
                        matches.append(
                            ToolContentRegexSearchMatch(
                                content_id=content_id,
                                match_start=matched.start(),
                                match_end=matched.end(),
                                window=window,
                            )
                        )
                        remaining -= count_canonical_tokens(window.text)
                        if len(matches) >= max_matches:
                            # max_matches 是业务数量上限，优先级高于继续消耗
                            # token 预算；当前已返回的窗口仍然是合法结果。
                            return tuple(matches), False
                except TimeoutError as exc:
                    raise ToolContentRegexTimeoutError(
                        f"regex search exceeded {_SEARCH_TIMEOUT_SECONDS}s"
                    ) from exc
            return tuple(matches), False

        matches, budget_exhausted = (
            await asyncio.to_thread(scan_loaded)
            if request.max_matches > 0
            else ((), False)
        )
        return ToolContentRegexSearchResult(
            matches=matches,
            failed=failed,
            budget_exhausted=budget_exhausted,
        )

    async def semantic_search(
        self,
        *,
        request: ToolContentSemanticSearchRequest,
        session_id: str,
    ) -> ToolContentSemanticSearchResult:
        """对已加载的内容 chunk 排名，并按排名顺序展开正文窗口。

        ranking 阶段只处理可检索文本和结构字段；正文窗口在排名之后生成，
        因此候选数量、排名数量和最终可返回窗口数量分别受 `top_k`、排序
        结果以及总 token 预算约束。
        """

        stored_items, failed = await self._load_many(
            content_ids=request.content_ids,
            session_id=session_id,
        )

        candidates: list[RankCandidate] = []
        sources: dict[
            str,
            tuple[str, StoredToolContent, ToolContentChunk],
        ] = {}
        for content_id, stored in stored_items:
            for chunk in stored.chunks:
                text = chunk_text(stored, chunk)
                if not text:
                    # 空 chunk 无法提供可检索文本，也不应占用 ranking 候选名额。
                    continue
                candidate_id = f"{content_id}:chunk:{chunk.chunk_index}"
                sources[candidate_id] = (content_id, stored, chunk)
                candidates.append(
                    RankCandidate(
                        candidate_id=candidate_id,
                        text=text,
                        fields={
                            "section": "\n".join(
                                " > ".join(path) for path in chunk.section_paths
                            ),
                            "anchor": "\n".join(chunk.anchor_labels),
                        },
                        group_key=content_id,
                    )
                )

        if not candidates or request.top_k <= 0:
            # 没有可检索候选或调用方明确要求零结果时，不启动 ranking。
            return ToolContentSemanticSearchResult(failed=failed)
        result = await self._ranking_pipeline.arank(
            RankRequest(
                query=RankQuery(text=request.query.strip()),
                candidates=tuple(candidates),
                top_k=request.top_k,
                candidate_limit=len(candidates),
            )
        )

        results: list[ToolContentSemanticSearchItem] = []
        remaining = self._semantic_search_total_token_budget
        budget_exhausted = False
        for item in result.ranked:
            if remaining <= 0:
                # ranking 结果仍然存在，但正文展开预算只允许返回前面的结果。
                budget_exhausted = True
                break
            source = sources.get(item.candidate_id)
            if source is None:
                continue
            content_id, stored, chunk = source
            window = self._semantic_search_window_builder.build_source_window(
                stored,
                chunk=chunk,
                token_budget=remaining,
            )
            results.append(
                ToolContentSemanticSearchItem(
                    content_id=content_id,
                    rank=item.rank,
                    score=item.score,
                    chunk_index=chunk.chunk_index,
                    window=window,
                )
            )
            remaining -= count_canonical_tokens(window.text)
        return ToolContentSemanticSearchResult(
            results=tuple(results),
            failed=failed,
            budget_exhausted=budget_exhausted,
        )

    async def _load_many(
        self,
        *,
        content_ids: tuple[str, ...],
        session_id: str,
    ) -> tuple[
        tuple[tuple[str, StoredToolContent], ...],
        tuple[ToolContentReadFailure, ...],
    ]:
        """并发加载多个 content，并将单个失败隔离为结构化结果。

        `asyncio.gather` 保留输入任务的返回顺序；随后按原始 `content_ids`
        重新配对，使成功内容和失败内容都能对应到请求中的具体 id。
        """

        async def load_one(
            content_id: str,
        ) -> tuple[StoredToolContent | None, ToolContentReadFailure | None]:
            try:
                stored = await self._store.get(
                    content_id=content_id,
                    session_id=session_id,
                )
            except Exception as exc:
                return None, ToolContentReadFailure(
                    content_id=content_id,
                    reason=type(exc).__name__,
                )
            if stored is None:
                return None, ToolContentReadFailure(
                    content_id=content_id,
                    reason="content_not_found",
                )
            return stored, None

        loaded_items = await asyncio.gather(
            *(load_one(content_id) for content_id in content_ids)
        )
        stored_items: list[tuple[str, StoredToolContent]] = []
        failed: list[ToolContentReadFailure] = []
        for content_id, (stored, failure) in zip(content_ids, loaded_items):
            # 失败项不阻断其他内容；调用方可以同时消费成功结果和失败明细。
            if failure is not None:
                failed.append(failure)
            elif stored is not None:
                stored_items.append((content_id, stored))
        return tuple(stored_items), tuple(failed)


def _snapshot_pages(
    locators: tuple[TextLocator, ...],
) -> tuple[ToolContentSnapshotPage, ...]:
    """把存储层 page locator 转换为 snapshot 的公开 page 入口。"""

    return tuple(
        ToolContentSnapshotPage(
            page_label=locator.name.removeprefix("page:"),
            start_offset=locator.start_offset,
            end_offset=locator.end_offset,
        )
        for locator in locators
        if locator.kind is LocatorKind.PAGE
    )


def _snapshot_anchors(
    locators: tuple[TextLocator, ...],
) -> tuple[ToolContentSnapshotAnchor, ...]:
    """把附着在原文范围上的 anchor 转换为 snapshot 元数据。"""

    return tuple(
        ToolContentSnapshotAnchor(
            anchor_label=locator.name.removeprefix("anchor:"),
            start_offset=locator.start_offset,
            end_offset=locator.end_offset,
        )
        for locator in locators
        if locator.kind is LocatorKind.ANCHOR
    )


def _snapshot_sections(
    locators: tuple[TextLocator, ...],
) -> tuple[ToolContentSnapshotSection, ...]:
    """将扁平 section locator 重建为按正文顺序排列的树。

    存储层只需要保存每个 section 的完整路径和字符范围；工具 snapshot
    则需要树形 children。这里先建立 path 到 locator 的索引，再按父路径
    分组和起始 offset 排序，最后递归组装，避免依赖 locator 的存储顺序。
    """

    section_locators = tuple(
        locator for locator in locators if locator.kind is LocatorKind.SECTION
    )
    locator_by_path = {
        tuple(locator.name.removeprefix("section:").split(" > ")): locator
        for locator in section_locators
    }
    children_by_parent: dict[
        tuple[str, ...],
        list[tuple[str, ...]],
    ] = {}
    for path in locator_by_path:
        children_by_parent.setdefault(path[:-1], []).append(path)
    for children in children_by_parent.values():
        # 同一父节点下的兄弟 section 按原文位置排序，而不是按字典序；
        # 这保证 snapshot 反映用户看到的文档顺序。
        children.sort(key=lambda path: locator_by_path[path].start_offset)

    def build(path: tuple[str, ...]) -> ToolContentSnapshotSection:
        """递归构建一个 section 节点及其直接子节点。"""

        locator = locator_by_path[path]
        return ToolContentSnapshotSection(
            title=path[-1],
            section_path=" > ".join(path),
            start_offset=locator.start_offset,
            end_offset=locator.end_offset,
            has_content=locator.end_offset > locator.start_offset,
            children=tuple(build(child) for child in children_by_parent.get(path, [])),
        )

    return tuple(build(path) for path in children_by_parent.get((), []))


def _regex_window_range(
    text: str,
    *,
    match_start: int,
    match_end: int,
    context_chars: int | None,
    context_side_token_budget: int,
    total_token_budget: int,
) -> tuple[int, int]:
    """计算包含 regex 命中且不超过总预算的原文字符范围。

    先按请求选择上下文：未指定 `context_chars` 时使用两侧 token 预算，
    指定后先按字符扩展。若候选范围仍超出总预算，再固定命中内容，
    从两侧按预算分配并做最终字符级回退。
    """

    if context_chars is None:
        # suffix/prefix helper 返回的是相对输入片段的字符 offset；这里的
        # before_start 仍然是完整文本前缀中的坐标，after_length 是尾部
        # 片段长度，所以可直接与 match_end 相加。
        _, before_start, _ = truncate_canonical_suffix(
            text[:match_start],
            context_side_token_budget,
        )
        _, after_length, _ = truncate_canonical_prefix(
            text[match_end:],
            context_side_token_budget,
        )
        candidate_start = before_start
        candidate_end = match_end + after_length
    else:
        context_chars = max(context_chars, 0)
        candidate_start = max(match_start - context_chars, 0)
        candidate_end = min(match_end + context_chars, len(text))

    if count_canonical_tokens(text[candidate_start:candidate_end]) <= total_token_budget:
        # 常见路径：上下文选择本身已经满足总预算，无需再次分配。
        return candidate_start, candidate_end

    match_tokens = count_canonical_tokens(text[match_start:match_end])
    if match_tokens >= total_token_budget:
        # 命中本身就达到预算时，保留完整命中优先于返回无关上下文。
        return match_start, match_end

    context_budget = total_token_budget - match_tokens
    before_budget = context_budget // 2
    _, before_offset, _ = truncate_canonical_suffix(
        text[candidate_start:match_start],
        before_budget,
    )
    start = candidate_start + before_offset
    after_budget = context_budget - count_canonical_tokens(text[start:match_start])
    _, after_length, _ = truncate_canonical_prefix(
        text[match_end:candidate_end],
        after_budget,
    )
    end = match_end + after_length

    # 预算按两侧独立估算后，tokenizer 仍可能在 start/match/end 边界产生
    # 合并差异，因此最终必须以完整窗口重新计数，而不是相信预算相加。
    while count_canonical_tokens(text[start:end]) > total_token_budget and end > match_end:
        end -= 1
    while count_canonical_tokens(text[start:end]) > total_token_budget and start < match_start:
        start += 1
    return start, end
