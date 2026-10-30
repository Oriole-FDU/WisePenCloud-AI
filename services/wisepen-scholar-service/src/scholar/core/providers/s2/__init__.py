"""Semantic Scholar 适配器的公开入口。"""

from .provider import S2Provider
from .transport import S2Transport

__all__ = ["S2Provider", "S2Transport"]
