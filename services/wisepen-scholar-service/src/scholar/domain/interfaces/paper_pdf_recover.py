"""按 DOI 精确寻找 PDF，不承担论文身份查询或 metadata 富化。"""

from typing import Protocol


class PaperPdfRecover(Protocol):
    async def recover(self, doi: str) -> str | None: ...
