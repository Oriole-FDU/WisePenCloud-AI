from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class WebSearchCandidateMapping:
    """web_search 候选项到真实 URL 的短期映射。"""

    user_id: str
    search_ref: str
    search_run_id: str
    candidate_id: str
    source_id: str
    url: str
    source_scope: str
    metadata: dict[str, object] = field(default_factory=dict)
