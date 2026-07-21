from __future__ import annotations

from typing import Protocol

from ..core.models import RawFetchOutput


class WebFetcher(Protocol):
    async def fetch(self, url: str) -> RawFetchOutput:
        ...
