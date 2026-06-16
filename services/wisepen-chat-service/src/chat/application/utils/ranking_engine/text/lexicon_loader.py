from __future__ import annotations

from .domain_lexicon import DomainLexicon
from .lexicon_sources import LexiconSource


class DomainLexiconLoader:
    """统一领域词表加载入口。"""

    __slots__ = ("sources",)

    def __init__(
        self,
        *,
        sources: tuple[LexiconSource, ...],
    ) -> None:
        self.sources = sources

    def load(self) -> DomainLexicon:
        """按当前配置加载、合并并返回领域词典。"""
        lexicon = DomainLexicon()

        for source in self.sources:
            lexicon = lexicon.merge(source.load())

        return lexicon.normalized()
