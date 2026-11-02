"""在最终返回前补齐 PDF；补救失败不撤销已找到的论文。"""

import asyncio
from dataclasses import replace

from redis.exceptions import RedisError

from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_pdf_recover import PaperPdfRecover
from scholar.domain.models.identifiers import normalize_doi
from scholar.domain.models.paper import Paper
from scholar.application.source_execution import (
    SourceExecution,
    SourceExecutionStatus,
)
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
    ) -> tuple[list[Paper], SourceExecution | None]:
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
            return papers, None

        # 批量查缓存，再对未命中的 DOI 并发补查上游。
        try:
            resolved = await self.cache.get_pdf_many(list(positions))
        except RedisError as error:
            return papers, SourceExecution(
                "unpaywall",
                False,
                SourceExecutionStatus.FAILED,
                str(error),
            )
        cache_hit = bool(resolved)
        # 完整论文缓存命中后只组装已有 PDF 事实，不再触发外部补全。
        pending = [] if cache_only else [doi for doi in positions if doi not in resolved]
        results = await asyncio.gather(
            *(self.provider.recover(doi) for doi in pending),
            return_exceptions=True,
        )

        errors, writes = [], []
        for doi, result in zip(pending, results, strict=True):
            if isinstance(result, BaseException):
                # 只隔离已知 ProviderError；程序错误和取消直接传播。
                if not isinstance(result, ProviderError):
                    raise result
                errors.append(result)
            else:
                resolved[doi] = result
                # 确认无 PDF 与找到 PDF 共用完整缓存周期；请求失败仍不缓存。
                writes.append((doi, result, self.ttl_seconds))

        if writes:
            try:
                await self.cache.put_pdf_many(writes)
            except RedisError as error:
                # 新找到的 PDF 仍可返回，写缓存故障单独报告。
                errors.append(error)

        # 回填 PDF：不覆盖已有 pdf_url 的论文（positions 只收集缺失者）。
        for doi, url in resolved.items():
            for paper in positions[doi]:
                paper.pdf_url = url

        status = SourceExecutionStatus.COMPLETED
        if errors:
            status = (
                SourceExecutionStatus.PARTIAL
                if resolved
                else SourceExecutionStatus.FAILED
            )
        return papers, SourceExecution(
            "unpaywall",
            cache_hit,
            status,
            str(errors[0]) if errors else None,
        )
