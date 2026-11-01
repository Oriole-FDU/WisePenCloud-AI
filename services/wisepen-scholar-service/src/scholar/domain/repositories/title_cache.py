"""标题到稳定身份的共享缓存；论文事实仍由来源缓存负责。"""

from typing import Protocol

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.models.titles import TitleMatch


class TitleCache(Protocol):
    async def get_many(self, titles: list[str]) -> list[TitleMatch]: ...

    async def put_many(self, entries: list[tuple[str, list[PaperIdentifier]]]) -> None: ...
