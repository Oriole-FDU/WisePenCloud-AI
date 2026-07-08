from __future__ import annotations

from chat.application.tools.search_tools.web_search.pipeline.candidate_selector import select_candidate_ids
from chat.application.tools.search_tools.web_search.pipeline.candidates_builder import (
    WebSearchCandidate,
)
from chat.application.utils.xml_markup import xml_attr, xml_cdata


async def select_recommended_ids(
        *,
        search_query: str,
        candidates: tuple[WebSearchCandidate, ...],
        max_recommended_candidates: int,
        fallback_candidates_count: int,
) -> tuple[str, ...]:
    if not candidates:
        return ()

    # 用小模型挑选最值得后续关注的候选结果
    selected = await select_candidate_ids(
        search_query=search_query,
        candidates_xml=_candidates_xml(candidates),
    )
    if selected:
        valid_ids = {candidate.candidate_id for candidate in candidates}
        filtered = tuple(candidate_id for candidate_id in selected if candidate_id in valid_ids)[
            :max_recommended_candidates
        ]
        if filtered:
            return filtered

    # 排序失败时的兜底策略：按原始顺序取前 N 条
    return tuple(candidate.candidate_id for candidate in candidates[:fallback_candidates_count])


def _candidates_xml(candidates: tuple[WebSearchCandidate, ...]) -> str:
    blocks: list[str] = []
    for candidate in candidates:
        parts = [
            f"    <candidate id=\"{xml_attr(candidate.candidate_id)}\">",
            f"      <title>{xml_cdata(candidate.title)}</title>",
            f"      <url>{xml_cdata(candidate.url)}</url>",
        ]
        if candidate.overview:
            parts.append(f"      <overview>{xml_cdata(candidate.overview)}</overview>")
        if candidate.highlights:
            parts.append("      <highlights>")
            parts.extend(
                f"        <highlight>{xml_cdata(highlight)}</highlight>"
                for highlight in candidate.highlights
            )
            parts.append("      </highlights>")
        parts.append("    </candidate>")
        blocks.append("\n".join(parts))
    return "\n".join(blocks)
