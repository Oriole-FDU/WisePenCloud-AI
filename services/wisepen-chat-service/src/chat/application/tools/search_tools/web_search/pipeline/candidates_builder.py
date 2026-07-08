from __future__ import annotations

from dataclasses import dataclass

from chat.application.tools.search_tools.web_search.providers.models import ProviderSearchResponse


@dataclass(frozen=True, slots=True)
class WebSearchCandidate:
    candidate_id: str  # [1] 形式的稳定候选编号，供后续模型引用
    title: str
    url: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class VisibleWebSearchCandidate:
    url: str
    title: str
    overview: str | None = None
    highlights: tuple[str, ...] = ()


def build_candidates(
        responses: tuple[ProviderSearchResponse, ...],
) -> tuple[WebSearchCandidate, ...]:
    """从 provider 响应构建候选列表，使用 [1]、[2] 形式的稳定编号。"""
    return tuple(
        WebSearchCandidate(
            candidate_id=f"[{i}]",
            title=item.title,
            url=item.url,
            overview=item.preview.overview,
            highlights=item.preview.highlights,
        )
        for i, item in enumerate(
            (item for resp in responses for item in resp.results),
            start=1,
        )
    )
