from __future__ import annotations

import json
from hashlib import sha256
from typing import Any

from chat.application.tools.common.chunking_engine import Chunk
from chat.application.tools.common.ranking_engine.core.models import RankedCandidate

from .models import (
    ContextBundle,
    ContextContent,
    ContextContentKind,
    ContextContentRole,
    ContextEvidence,
)


class ContextAdapter:
    """把常见工具输出适配成 ContextBundle。"""

    __slots__ = ()

    def from_text(
        self,
        text: str,
        *,
        content_id: str | None = None,
        title: str = "",
        role: str = ContextContentRole.TOOL_RESULT,
        kind: str = ContextContentKind.MARKDOWN,
        metadata: dict[str, Any] | None = None,
    ) -> ContextBundle:
        """把单段文本包装成 bundle。"""
        return ContextBundle(
            contents=(
                ContextContent(
                    content_id=content_id or _content_id(text=text, role=role, kind=kind, title=title),
                    text=text,
                    title=title,
                    role=role,
                    kind=kind,
                    metadata=dict(metadata or {}),
                ),
            )
        )

    def from_payload(
        self,
        payload: Any,
        *,
        content_id: str | None = None,
        title: str = "Payload",
        metadata: dict[str, Any] | None = None,
    ) -> ContextBundle:
        """把结构化 payload 渲染为 JSON 正文。"""
        return self.from_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            content_id=content_id,
            title=title,
            role=ContextContentRole.PAYLOAD,
            kind=ContextContentKind.JSON,
            metadata=metadata,
        )

    def from_chunks(
        self,
        chunks: tuple[Chunk, ...],
        *,
        title: str = "",
        content_id_prefix: str = "chunk",
        role: str = ContextContentRole.WINDOW,
    ) -> ContextBundle:
        """把 chunking 结果中的 chunk 转成可渲染正文窗口。"""
        return ContextBundle(
            contents=tuple(
                ContextContent(
                    content_id=chunk.chunk_id
                    or _content_id(
                        text=chunk.text,
                        role=role,
                        kind=ContextContentKind.MARKDOWN,
                        title=f"{content_id_prefix}_{index}",
                    ),
                    text=chunk.text,
                    title=title,
                    role=role,
                    order=index,
                    metadata={
                        **dict(chunk.metadata),
                        "chunk_index": chunk.chunk_index,
                        "level": chunk.level,
                        "parent_chunk_id": chunk.parent_chunk_id,
                        "start_offset": chunk.start_offset,
                        "end_offset": chunk.end_offset,
                    },
                )
                for index, chunk in enumerate(chunks)
                if chunk.text
            )
        )

    def from_ranked_candidates(
        self,
        ranked: tuple[RankedCandidate, ...],
        *,
        excerpt_limit: int = 800,
    ) -> ContextBundle:
        """把 ranking 结果转成证据摘要 bundle。"""
        return ContextBundle(
            evidence=tuple(
                ContextEvidence(
                    evidence_id=f"evidence_{item.rank}",
                    title=item.candidate.metadata.get("title", ""),
                    excerpt=_truncate(item.candidate.text, excerpt_limit),
                    content_id=item.candidate.metadata.get("content_id"),
                    content_role=item.candidate.metadata.get("content_role"),
                    chunk_index=item.candidate.metadata.get("chunk_index"),
                    source_id=item.candidate.metadata.get("source_id"),
                    url=item.candidate.metadata.get("url"),
                    score=item.score,
                    metadata={
                        "rank": item.rank,
                        "candidate_id": item.candidate_id,
                        "reason": item.reason,
                    },
                )
                for item in ranked
            )
        )


def _content_id(*, text: str, role: str, kind: str, title: str) -> str:
    seed = "\n".join((role, kind, title, text))
    return f"ctx_{sha256(seed.encode()).hexdigest()[:16]}"


def _truncate(text: str, limit: int) -> str:
    normalized = " ".join(text.split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."
