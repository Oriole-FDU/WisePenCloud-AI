from __future__ import annotations

from .base import WebFetcher
from .static_page_fetcher import StaticPageFetcher
from .stealthy_page_fetcher import StealthyPageFetcher

__all__ = [
    "StaticPageFetcher",
    "StealthyPageFetcher",
    "WebFetcher",
]
