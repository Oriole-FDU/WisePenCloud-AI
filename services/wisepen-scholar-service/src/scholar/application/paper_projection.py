"""为模型制作论文出口副本；内部查询、别名和缓存始终使用完整事实。"""

from dataclasses import replace
from typing import Any

from scholar.domain.models.paper import Paper
from scholar.providers.policies import PROVIDER_POLICIES


def _compact(value: Any) -> Any:
    """递归省略 JSON 空值；零引用、零点赞和 false 都是有效事实。"""
    if isinstance(value, dict):
        return {
            key: compact
            for key, item in value.items()
            if (compact := _compact(item)) not in (None, "", [], {})
        }
    if isinstance(value, list):
        return [
            compact
            for item in value
            if (compact := _compact(item)) not in (None, "", [], {})
        ]
    return value


def project_paper(paper: Paper) -> Paper:
    """统一装配各来源投影，不修改原论文及其嵌套 metadata。"""
    metadata = {
        source: policy.project_metadata(paper)
        for source, policy in PROVIDER_POLICIES.items()
        if source in paper.metadata
    }
    authors = (
        [f"{paper.authors[0]} et al."] if len(paper.authors) > 1 else paper.authors[:]
    )
    return replace(paper, authors=authors, metadata=_compact(metadata))
