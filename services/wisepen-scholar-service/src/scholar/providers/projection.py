"""论文出口的紧凑投影；来源查找保留完整事实，返回副本才裁剪。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from scholar.domain.models.paper import Paper


def compact_authors(authors: list[str]) -> list[str]:
    return [f"{authors[0]} et al."] if len(authors) > 1 else authors[:]


def project_paper(paper: Paper) -> Paper:
    """制作紧凑出口副本；S2 抓取时间和摘要模型留在来源事实中。"""
    metadata = deepcopy(paper.metadata)
    s2 = metadata.get("s2")
    if s2 is not None:
        # 底库只同步 S2：每日 API 同步含 observed_at，release 导入则没有。
        s2.pop("observed_at", None)
        if s2.get("tldr") is not None:
            s2["tldr"].pop("model", None)
    return replace(paper, authors=compact_authors(paper.authors), metadata=metadata)
