from __future__ import annotations

import re

from chat.application.tools.common.tool_content_store import ToolContentStore
from chat.application.tools.common.tool_content_store.models import StoredToolContent, ToolContentChunk
from chat.application.tools.session_tools.tool_content_read.models import (
    ToolContentReadItemResult,
    ToolContentReadMode,
    ToolContentReadRequest,
    ToolContentSelector,
    ToolContentWindow,
)
from chat.application.tools.session_tools.utils.content_window_builder import ToolContentWindowBuilder
from chat.application.tools.tool_settings import tool_settings
from chat.application.utils.ranking_engine import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
)
from chat.application.utils.ranking_engine import get_ranking_engine

# 正则模式串最大字符数限制，防止 DoS 攻击引发灾难性回溯
MAX_REGEX_PATTERN_CHARS = tool_settings.TOOL_CONTENT_READ_MAX_REGEX_PATTERN_CHARS


class ToolContentReadService:
    """工具内容分流读取服务，支持连续切片、重排检索与正则匹配三种模式。"""

    __slots__ = ("_store", "_ranking_engine")

    def __init__(
            self,
            *,
            store: ToolContentStore,
            ranking_engine: RankingEngine | None = None,
    ) -> None:
        self._store = store
        self._ranking_engine = ranking_engine or get_ranking_engine("services.ranked_expand")

    async def read(
            self,
            *,
            request: ToolContentReadRequest,
            session_id: str,
    ) -> tuple[ToolContentReadItemResult, ...]:
        """批量读取工具内容并构建对应的上下文文本窗口。"""
        self._validate_request(request)

        items = []
        for content_id in request.content_ids:
            item = await self._read_one(content_id=content_id, request=request, session_id=session_id)
            items.append(item)

        return tuple(items)

    @staticmethod
    def _validate_request(request: ToolContentReadRequest) -> None:
        """全局参数合法性预校验。"""
        if request.mode == ToolContentReadMode.RANKED_EXPAND and not (request.query or "").strip():
            raise ValueError("ranked_expand requires query.")

        if request.mode == ToolContentReadMode.REGEX_MATCH:
            pattern = request.pattern or ""
            if not pattern:
                raise ValueError("regex_match requires pattern.")
            if len(pattern) > MAX_REGEX_PATTERN_CHARS:
                raise ValueError(f"regex pattern is too long; max {MAX_REGEX_PATTERN_CHARS} chars.")

    async def _read_one(
            self,
            *,
            content_id: str,
            request: ToolContentReadRequest,
            session_id: str,
    ) -> ToolContentReadItemResult:
        """处理单条内容凭证。异常捕获降级为 failed 单项，不阻塞批处理。"""
        try:
            # 自动解析重定向收据以获取真实内容标识
            canonical_id, _ = await self._store.canonicalize_content_id(
                content_id=content_id,
                session_id=session_id,
            )
            stored = await self._store.get(content_id=canonical_id, session_id=session_id)

            if stored is None:
                return ToolContentReadItemResult(
                    content_id=canonical_id,
                    status="failed",
                    reason="content_not_found",
                )

            # 按指定的读取模式进行核心业务流分发
            if request.mode == ToolContentReadMode.CONTINUOUS:
                windows = (self._read_continuous(stored, request),)

            elif request.mode == ToolContentReadMode.RANKED_EXPAND:
                chunks = self._select_chunks(stored, request.selector)
                windows = await self._read_ranked_expand(stored, request, chunks)

            elif request.mode == ToolContentReadMode.REGEX_MATCH:
                chunks = self._select_chunks(stored, request.selector)
                windows = self._read_regex_match(stored, request, chunks)

            else:
                raise ValueError(f"Unsupported read mode: {request.mode}")

            return ToolContentReadItemResult(
                content_id=canonical_id,
                status="success",
                windows=windows,
            )

        except Exception as e:
            return ToolContentReadItemResult(
                content_id=content_id,
                status="failed",
                reason=e.__class__.__name__,
            )

    def _read_continuous(
            self,
            stored: StoredToolContent,
            request: ToolContentReadRequest,
    ) -> ToolContentWindow:
        """连续切片模式：基于字符偏置与长度进行轻量物理切片。"""
        offset = max(request.offset or 0, 0)
        limit = max(request.limit or 4000, 0)
        end = min(len(stored.text), offset + limit)

        return ToolContentWindow(
            text=stored.text[offset:end],
            start_offset=offset,
            end_offset=end,
        )

    async def _read_ranked_expand(
            self,
            stored: StoredToolContent,
            request: ToolContentReadRequest,
            candidate_chunks: tuple[ToolContentChunk, ...],
    ) -> tuple[ToolContentWindow, ...]:
        """重排扩展模式：计算分块语义相关性，并对 Top-K 命中项进行窗口包裹混叠。"""
        query = (request.query or "").strip()

        # 构建高密度特征排序候选集（过滤空分块）
        candidates = tuple(
            RankCandidate(
                candidate_id=f"{stored.content_id}:chunk:{chunk.chunk_index}",
                text=ToolContentWindowBuilder.chunk_text(stored, chunk),
                fields={
                    "section": " / ".join(chunk.section_path),
                    "anchor": " ".join(chunk.anchor_names),
                },
                metadata={
                    "content_id": stored.content_id,
                    "chunk_index": chunk.chunk_index,
                    "section_path": chunk.section_path,
                    "anchor_names": chunk.anchor_names,
                },
            )
            for chunk in candidate_chunks
            if ToolContentWindowBuilder.chunk_text(stored, chunk)
        )
        if not candidates:
            return ()

        # 调度上游双塔重排或精排引擎
        ranked = (
            self._ranking_engine.rank(
                RankRequest(
                    query=RankQuery(text=query),
                    candidates=candidates,
                    top_k=max(request.top_k, 0),
                    candidate_limit=max(request.top_k, 0),
                )
            )
        ).ranked

        # 提取重排索引并进行前后向滑动窗口合并
        return tuple(
            ToolContentWindowBuilder.expand(
                stored,
                center_chunk=int(item.candidate.metadata["chunk_index"]),
                merge_before=request.merge_before,
                merge_after=request.merge_after,
            )
            for item in ranked
        )

    def _read_regex_match(
            self,
            stored: StoredToolContent,
            request: ToolContentReadRequest,
            candidate_chunks: tuple[ToolContentChunk, ...],
    ) -> tuple[ToolContentWindow, ...]:
        """正则匹配模式：利用流式迭代器定位正则关键词，并对命中分块展开上下文。"""
        pattern = request.pattern or ""
        regex = re.compile(pattern)

        windows: list[ToolContentWindow] = []
        seen_centers: set[int] = set()  # 去重防御：避免多词重复命中同一分块

        for chunk in candidate_chunks:
            text = ToolContentWindowBuilder.chunk_text(stored, chunk)

            for _ in regex.finditer(text):
                if chunk.chunk_index in seen_centers:
                    continue

                seen_centers.add(chunk.chunk_index)
                windows.append(
                    ToolContentWindowBuilder.expand(
                        stored,
                        center_chunk=chunk.chunk_index,
                        merge_before=request.merge_before,
                        merge_after=request.merge_after,
                    )
                )

                if len(windows) >= max(request.max_matches, 0):
                    return tuple(windows)

        return tuple(windows)

    def _select_chunks(
            self,
            stored: StoredToolContent,
            selector: ToolContentSelector | None,
    ) -> tuple[ToolContentChunk, ...]:
        """基于选择器策略降级筛选候选数据分块。"""
        chunks = tuple(sorted(stored.chunks, key=lambda c: c.chunk_index))
        if selector is None:
            return chunks

        selected: set[int] | None = None

        # 策略 1：显式索引切片匹配
        if selector.chunk_indices:
            selected = set(selector.chunk_indices)

        # 策略 2：基于逻辑目录、页面或锚点索引的多路交集计算
        indexed = self._index_selected_chunks(stored, selector)
        if indexed is not None:
            selected = indexed if selected is None else selected & indexed

        # 策略 3：基于单元特定文本类型的映射提取
        if selected is None and selector.unit_types:
            selected = {
                c.chunk_index
                for c in chunks
                if set(selector.unit_types) & set(c.unit_types)
            }

        # 策略 4：未设置有效过滤域时，默认回退至全量块匹配
        if selected is None:
            selected = {c.chunk_index for c in chunks}

        # 基于未知数据源标识符 (unknown_type) 进行终期过滤
        result = []
        for chunk in chunks:
            if chunk.chunk_index not in selected:
                continue
            if selector.unit_types and not selector.include_unknown and not chunk.unit_types:
                continue
            result.append(chunk)

        return tuple(result)

    def _index_selected_chunks(
            self,
            stored: StoredToolContent,
            selector: ToolContentSelector,
    ) -> set[int] | None:
        """基于目录结构多级条目进行严苛交集（AND）剪枝过滤。"""
        selected: set[int] | None = None

        for prefix, values in (
                ("section", selector.sections),
                ("page", selector.pages),
                ("anchor", selector.anchors),
        ):
            if not values:
                continue

            matched: set[int] = set()
            for entry in (stored.index.entries if stored.index else ()):
                name = entry.name
                bare_name = name.split(":", 1)[1] if ":" in name else name

                # 支持命名空间全匹配、纯标签短名匹配及子串匹配
                if any(v == name or v == bare_name or v in bare_name for v in values):
                    if name.startswith(f"{prefix}:") or prefix in name:
                        matched.update(entry.chunk_indices)

            selected = matched if selected is None else selected & matched

        return selected
