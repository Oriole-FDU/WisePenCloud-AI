from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


WEB_FETCH_REFRESH_JOB = "refresh_web_fetch_cache"
DOCUMENT_PARSE_REFRESH_JOB = "refresh_document_parse_cache"


@dataclass(frozen=True, slots=True)
class WebContentCacheRefreshJob:
    name: str
    payload: dict[str, object] = field(default_factory=dict)
    job_id: str | None = None


class WebContentCacheRefreshTaskPublisher(Protocol):
    async def enqueue(self, job: WebContentCacheRefreshJob) -> None:
        ...
