"""将一批完整标题解析为稳定身份候选的外部边界。"""

from typing import Protocol

from scholar.domain.models.titles import TitleMatch


class TitleResolver(Protocol):
    async def match(self, titles: list[str], *, deadline: float | None = None) -> list[TitleMatch]: ...
