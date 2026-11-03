"""在最终返回前补齐 PDF；补救失败不撤销已找到的论文。"""

import asyncio
from dataclasses import replace

from redis.exceptions import RedisError

from common.logger import warning
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_pdf_recover import PaperPdfRecover
from scholar.domain.models.identifiers import normalize_doi
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_cache import PaperCache
from scholar.core.providers import PROVIDER_POLICIES


class PaperPdfRecoverService:
    def __init__(
        self,
        provider: PaperPdfRecover,
        cache: PaperCache,
        *,
        ttl_seconds: int,
    ):
        self.provider, self.cache = provider, cache
        self.ttl_seconds = ttl_seconds

    async def recover(
        self,
        papers: list[Paper],
        *,
        cache_only: bool = False,
        deadline: float | None = None,
    ) -> list[Paper]:
        # 只改副本；来源和候选缓存仍保留投影前的原始事实。
        papers = [replace(paper) for paper in papers]

        # 先尝试各 provider 的策略补齐 PDF；仍缺失时按 arXiv / DOI 兜底。
        positions: dict[str, list[Paper]] = {}
        for paper in papers:
            if not paper.pdf_url:
                paper.pdf_url = next(
                    (
                        url
                        for policy in PROVIDER_POLICIES.values()
                        if policy.pdf_url is not None
                        and (url := policy.pdf_url(paper))
                    ),
                    None,
                )
            if not paper.pdf_url and paper.arxiv_id:
                paper.pdf_url = f"https://arxiv.org/pdf/{paper.arxiv_id}"
            if not paper.pdf_url and paper.doi:
                positions.setdefault(normalize_doi(paper.doi), []).append(paper)
        if not positions:
            return papers

        # 读取与写入分别使用剩余预算，避免写入超时被误判为 PDF 读取失败。
        budget = asyncio.timeout_at(deadline)
        try:
            async with budget:
                resolved = await self.cache.get_pdf_many(list(positions))
                # 完整论文缓存命中后只组装已有 PDF 事实，不再触发外部补全。
                pending = [] if cache_only else [doi for doi in positions if doi not in resolved]
                results = await asyncio.gather(
                    *(self.provider.recover(doi) for doi in pending),
                    return_exceptions=True,
                )
        except RedisError as error:
            warning("Scholar PDF recovery failed.", source="unpaywall", operation="paper_cache.get_pdf_many", exc=error)
            return papers
        except TimeoutError as error:
            if not budget.expired():
                raise
            # PDF 是自动补全，预算耗尽只审计，不能使已有论文变成部分失败。
            warning("Scholar PDF recovery timed out.", source="unpaywall", exc=error)
            return papers

        writes = []
        for doi, result in zip(pending, results, strict=True):
            if isinstance(result, BaseException):
                # 只隔离已知 ProviderError；程序错误和取消直接传播。
                if not isinstance(result, ProviderError):
                    raise result
                warning("Scholar PDF recovery failed.", source="unpaywall", doi=doi,
                        exc=result, cause=result.__cause__)
            else:
                resolved[doi] = result
                # 确认无 PDF 与找到 PDF 共用完整缓存周期；请求失败仍不缓存。
                writes.append((doi, result, self.ttl_seconds))

        # 回填 PDF：不覆盖已有 pdf_url 的论文（positions 只收集缺失者）。
        for doi, url in resolved.items():
            for paper in positions[doi]:
                paper.pdf_url = url

        if writes:
            try:
                async with asyncio.timeout_at(deadline):
                    await self.cache.put_pdf_many(writes)
            except (RedisError, TimeoutError) as error:
                # 新找到的 PDF 已回填；写入故障不改变上游补全的执行状态。
                warning("Scholar cache write failed.", source="unpaywall", operation="paper_cache.put_pdf_many",
                        error_type=type(error).__name__, error=str(error))

        return papers
