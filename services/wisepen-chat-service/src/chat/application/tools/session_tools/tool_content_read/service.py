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
from chat.application.utils.ranking_engine import (
    RankCandidate,
    RankQuery,
    RankRequest,
    RankingEngine,
)
from chat.application.utils.ranking_engine.factory import get_ranking_engine

# 正则模式串最大字符数限制，防止 DoS
MAX_REGEX_PATTERN_CHARS = 500


class ToolContentReadService:
    """ToolContent 读取服务，实现 selector 预处理 + 三种 read mode 的具体逻辑。

    三种读取模式：
    - continuous：按字符 offset/limit 直接从原文切片
    - ranked_expand：对候选 chunks 做语义排序，取 Top-K 展开窗口
    - regex_match：对候选 chunks 做正则匹配，命中的 chunk 展开窗口
    """

    __slots__ = ("_store", "_ranking_engine")

    def __init__(
        self,
        *,
        store: ToolContentStore,
        ranking_engine: RankingEngine | None = None,
    ) -> None:
        self._store = store
        # 默认使用注册表中预注册的 ranked_expand 排序引擎
        self._ranking_engine = ranking_engine or get_ranking_engine("services.ranked_expand")

    async def read(
        self,
        *,
        request: ToolContentReadRequest,
        session_id: str,
    ) -> tuple[ToolContentReadItemResult, ...]:
        """读取 ToolContent，并返回结构化窗口。

        流程：
        1. 调用 canonicalize_content_id 自动解析可能的重定向收据
        2. 从 Store 读取完整内容
        3. 根据 request.mode 分发到不同的读取方法
        4. 返回一个或多个 ToolContentReadItemResult
        """
        self._validate_request(request)
        items = []
        for content_id in request.content_ids:
            items.append(await self._read_one(content_id=content_id, request=request, session_id=session_id))

        return tuple(items)

    @staticmethod
    def _validate_request(request: ToolContentReadRequest) -> None:
        """校验批量读取请求中对整批都生效的参数。"""
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
        """读取单个 content_id；单项失败转为 failed item。"""
        try:
            # 自动解析：如果 content_id 是重定向收据，得到真正的可读 content_id。
            canonical_content_id, _ = await self._store.canonicalize_content_id(
                content_id=content_id,
                session_id=session_id,
            )
            stored = await self._store.get(content_id=canonical_content_id, session_id=session_id)
            if stored is None:
                return ToolContentReadItemResult(
                    content_id=canonical_content_id,
                    status="failed",
                    reason="content_not_found",
                )

            # 同一批 content_ids 共用同一组读取参数。
            if request.mode == ToolContentReadMode.CONTINUOUS:
                windows = (self._read_continuous(stored, request),)
            elif request.mode == ToolContentReadMode.RANKED_EXPAND:
                windows = await self._read_ranked_expand(stored, request, self._select_chunks(stored, request.selector))
            elif request.mode == ToolContentReadMode.REGEX_MATCH:
                windows = self._read_regex_match(stored, request, self._select_chunks(stored, request.selector))
            else:
                raise ValueError(f"Unsupported read mode: {request.mode}")

            return ToolContentReadItemResult(
                content_id=canonical_content_id,
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
        """连续读取模式：从原文按字符 offset/limit 切片。"""
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
        """排名展开模式：用 query 对候选 chunks 做语义排序，取 Top-K 展开。"""
        query = (request.query or "").strip()

        # 构建排序候选：每个 chunk 作为一个 RankCandidate，附带结构化字段
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
            if ToolContentWindowBuilder.chunk_text(stored, chunk)  # 跳过空 chunk
        )
        if not candidates:
            return ()

        # 调用排序引擎
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

        # 对每个排序结果展开 merge 窗口，携带 rank 和 score
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
        """正则匹配模式：在候选 chunks 中执行正则，命中则展开窗口。"""
        pattern = request.pattern or ""
        regex = re.compile(pattern)
        windows: list[ToolContentWindow] = []
        seen_centers: set[int] = set()  # 去重：同一个 chunk 只展开一次
        for chunk in candidate_chunks:
            text = ToolContentWindowBuilder.chunk_text(stored, chunk)
            for match in regex.finditer(text):
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
        """根据 selector 从 stored.chunks 中筛选出候选 chunk 列表。

        筛选逻辑（按优先级）：
        1. chunk_indices 显式指定 → 精准匹配
        2. selector 中有 section/page/anchor → 通过索引匹配
        3. selector 中有 unit_types → 按 unit type 匹配
        4. 没有 selector → 返回全部 chunks

        最后再用 include_unknown 过滤掉"无结构元数据"的 chunk。
        """
        chunks = tuple(sorted(stored.chunks, key=lambda chunk: chunk.chunk_index))
        if selector is None:
            return chunks

        selected: set[int] | None = None
        # 优先级 1：显式指定的 chunk_indices
        if selector.chunk_indices:
            selected = set(selector.chunk_indices)

        # 优先级 2：通过结构化索引（section/page/anchor）匹配
        indexed = self._index_selected_chunks(stored, selector)
        if indexed is not None:
            selected = indexed if selected is None else selected & indexed

        # 优先级 3：通过 unit_types 匹配
        if selected is None and selector.unit_types:
            selected = {
                chunk.chunk_index
                for chunk in chunks
                if set(selector.unit_types) & set(chunk.unit_types)
            }

        # 没有选中任何条件 → 默认全选
        if selected is None:
            selected = {chunk.chunk_index for chunk in chunks}

        # 如果指定了 unit_types 且 include_unknown=False，排除无 unit_types 的 chunk
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
        """通过 stored.index（结构索引）按 section/page/anchor 名称匹配 chunk。

        多个条件同时存在时取交集（AND 逻辑），
        没有索引条目或没有匹配时返回 None。
        """
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
                # 支持精准匹配和子串匹配
                if any(value == name or value == bare_name or value in bare_name for value in values):
                    if name.startswith(f"{prefix}:") or prefix in name:
                        matched.update(entry.chunk_indices)
            selected = matched if selected is None else selected & matched
        return selected
