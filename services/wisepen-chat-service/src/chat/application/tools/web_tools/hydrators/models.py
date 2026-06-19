from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class HydrationStatus(StrEnum):
    """内部补全状态。"""

    HYDRATED = "hydrated"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HydratedPaper:
    """OpenAlex 论文元数据补全结果。"""

    status: HydrationStatus
    title: str | None = None
    authors: tuple[str, ...] = ()
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    openalex_id: str | None = None
    abstract: str | None = None
    landing_url: str | None = None
    pdf_url: str | None = None
    open_access: bool | None = None
    cited_by_count: int | None = None
    concepts_or_topics: tuple[str, ...] = ()
    source_updated_at: str | None = None
    failure_reason: str | None = None


@dataclass(frozen=True, slots=True)
class HydratedGitHubRepository:
    """GitHub 仓库元数据补全结果。"""

    status: HydrationStatus
    full_name: str | None = None
    owner: str | None = None
    name: str | None = None
    description: str | None = None
    html_url: str | None = None
    homepage: str | None = None
    default_branch: str | None = None
    language: str | None = None
    topics: tuple[str, ...] = ()
    license: str | None = None
    stars: int | None = None
    forks: int | None = None
    open_issues: int | None = None
    pushed_at: str | None = None
    updated_at: str | None = None
    failure_reason: str | None = None
