"""用真实 PDF 编排服务隔离外部网络和缓存，供其他用例测试复用。"""

from unittest.mock import AsyncMock

from scholar.application.pdf_recover import PaperPdfRecoverService


class MemoryPdfCache:
    def __init__(self):
        self.values = {}
        self.writes = []

    async def get_many(self, dois):
        return {doi: self.values[doi] for doi in dois if doi in self.values}

    async def put_many(self, entries):
        self.writes.extend(entries)
        self.values.update((doi, url) for doi, url, _ in entries)


def make_pdf_service(provider=None, cache=None):
    return PaperPdfRecoverService(
        provider=provider or AsyncMock(recover=AsyncMock(return_value=None)),
        cache=cache or MemoryPdfCache(),
        positive_ttl_seconds=86400,
        negative_ttl_seconds=3600,
    )
