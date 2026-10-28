"""独立的 DOI 阅读链接缓存；键存在且值为空表示已确认无 PDF。"""

from typing import Protocol


class PdfCache(Protocol):
    async def get_many(self, dois: list[str]) -> dict[str, str | None]: ...

    async def put_many(self, entries: list[tuple[str, str | None, int]]) -> None: ...
