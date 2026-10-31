"""arXiv 在线搜索适配；领域到分类的转换统一由 field_mapping 负责。"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import replace
from datetime import date
from time import monotonic

from httpx import TransportError as HttpxTransportError
from openai import APIConnectionError, APIStatusError
from qdrant_client.http.exceptions import ApiException as QdrantApiError

from common.logger import warning
from common.utils.ai_clients.embedding import EmbeddingClient
from scholar.domain.errors import (
    ProviderError,
    provider_error_message,
    provider_operation,
)
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.local_paper_repository import LocalPaperRepository
from scholar.ml.specter import MODEL_SIGNATURE

# 领域 → arXiv 分类映射；None 表示该领域不支持 arXiv 搜索，空列表表示不限分类。
ARXIV_CATEGORIES: dict[AcademicField, list[str] | None] = {
    AcademicField.GENERAL: [],
    AcademicField.COMPUTER_SCIENCE: ["cs.*"],
    AcademicField.MEDICINE: None,
    AcademicField.BIOLOGY: ["q-bio.*"],
    AcademicField.ENGINEERING: ["eess.*"],
    AcademicField.ECONOMICS_BUSINESS: ["econ.*", "q-fin.*"],
    AcademicField.MATHEMATICS: ["math.*", "stat.*"],
    AcademicField.PHYSICS: [
        "physics.*",
        "astro-ph.*",
        "cond-mat.*",
        "gr-qc",
        "hep-ex",
        "hep-lat",
        "hep-ph",
        "hep-th",
        "math-ph",
        "nlin.*",
        "nucl-ex",
        "nucl-th",
        "quant-ph",
    ],
    AcademicField.CHEMISTRY: ["physics.chem-ph"],
    AcademicField.PSYCHOLOGY: None,
    AcademicField.SOCIAL_SCIENCES: ["physics.soc-ph"],
    AcademicField.ARTS_HUMANITIES: None,
    AcademicField.LAW: None,
    AcademicField.MATERIALS_ENVIRONMENTAL_SCIENCES: [
        "cond-mat.mtrl-sci",
        "physics.ao-ph",
        "physics.geo-ph",
    ],
}


def project_metadata(paper: Paper) -> dict:
    """保留分类及作者发表线索；仅当提交日期不同于顶层日期时补入。"""
    values = paper.metadata["arxiv"]
    result = {
        name: values.get(name)
        for name in ("categories", "journal_ref", "comments")
    }

    publication_date = (
        paper.publication_date.isoformat()
        if paper.publication_date is not None
        else None
    )
    if values.get("submitted_date") != publication_date:
        result["submitted_date"] = values.get("submitted_date")
    return result


class ArxivProvider:
    def __init__(
        self,
        paper_repository: LocalPaperRepository,
        embedding_client: EmbeddingClient,
        *,
        candidate_limit: int = 200,
        num_candidates: int = 1000,
        rrf_k: int = 60,
        citation_weight: float = 0.20,
        influential_weight: float = 0.10,
    ):
        self.paper_repository = paper_repository
        self.embedding_client = embedding_client
        self.candidate_limit = candidate_limit
        self.num_candidates = num_candidates
        self.rrf_k = rrf_k
        self.citation_weight = citation_weight
        self.influential_weight = influential_weight

    @property
    def cache_revision(self) -> str:
        """调参、模型或算法版本变化时使旧排名缓存失效。"""
        return hashlib.sha256(
            json.dumps(
                {
                    "algorithm": "qdrant-bm25-specter2-native-rrf-v1",
                    "model": MODEL_SIGNATURE,
                    "candidates": self.candidate_limit,
                    "num_candidates": self.num_candidates,
                    "rrf_k": self.rrf_k,
                    "citation_weight": self.citation_weight,
                    "influential_weight": self.influential_weight,
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()

    async def lookup(
        self,
        ids: list[PaperIdentifier],
        *,
        api_key: str | None = None,
    ) -> list[PaperLookupMatch]:
        """按 arXiv/DOI 身份透传仓储结果，保留歧义与逐项失败。"""
        try:
            matches = await self.paper_repository.lookup(self._normalize_ids(ids))
        except QdrantApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        return [
            replace(
                match,
                papers=[self._with_arxiv_doi(paper) for paper in match.papers],
            )
            for match in matches
        ]

    async def search(
        self,
        *,
        query: str,
        field: AcademicField,
        published_after: date | None,
        published_before: date | None,
        limit: int,
        api_key: str | None = None,
    ) -> PaperSearchBatch:
        categories = ARXIV_CATEGORIES[field]
        if categories is None:
            raise ValueError(f"arXiv search is not mapped for {field.value}")

        # 分阶段埋点：编码 → qdrant 检索；取消时可按当前 stage 记录耗时。
        stage = "embedding"
        provider_operation.set(stage)
        started = monotonic()
        try:
            # 编码失败降级为纯 BM25 召回，并显式标记 partial。
            partial_error = None
            try:
                vector = (await self.embedding_client.embed(texts=[query]))[0]
            except (APIConnectionError, APIStatusError) as error:
                vector = None
                partial_error = provider_error_message(error)

            stage = "qdrant.search"
            provider_operation.set(stage)
            started = monotonic()
            candidates = await self.paper_repository.search(
                query=query,
                vector=vector,
                categories=categories,
                published_after=published_after,
                published_before=published_before,
                limit=self.candidate_limit,
                num_candidates=self.num_candidates,
                rrf_k=self.rrf_k,
            )

            # 引用先验只对已召回候选做有界平滑加分；零和缺失不会抹掉相关性分数。
            def boosted_score(candidate):
                metrics = candidate.paper.metadata.get("s2", {})
                citations = metrics.get("citation_count") or 0
                influential = metrics.get("influential_citation_count") or 0
                return candidate.score * (
                    1
                    + self.citation_weight * citations / (citations + 100)
                    + self.influential_weight * influential / (influential + 10)
                )

            ranked = sorted(candidates, key=boosted_score, reverse=True)
            return PaperSearchBatch(
                [
                    self._with_arxiv_doi(candidate.paper)
                    for candidate in ranked[:limit]
                ],
                partial_error,
            )
        except asyncio.CancelledError:
            warning(
                "arXiv search interrupted.",
                stage=stage,
                elapsed_seconds=round(monotonic() - started, 3),
            )
            raise
        except (QdrantApiError, HttpxTransportError) as error:
            raise ProviderError(
                f"arxiv {stage}: {provider_error_message(error)}"
            ) from error

    @staticmethod
    def _normalize_ids(ids: list[PaperIdentifier]) -> list[PaperIdentifier]:
        """预印本 DOI 可由 arXiv ID 反推，本地仓储只以 arXiv ID 保存该身份。"""
        return [
            PaperIdentifier(
                IdentifierKind.ARXIV,
                normalize_arxiv(identifier.value.removeprefix("10.48550/arxiv.")),
            )
            if identifier.kind == IdentifierKind.DOI
            and identifier.value.startswith("10.48550/arxiv.")
            else identifier
            for identifier in ids
        ]

    @staticmethod
    def _with_arxiv_doi(paper: Paper) -> Paper:
        """为仅有 arXiv ID 的论文补全官方 DOI（``10.48550/arXiv.<ID>``）。"""
        if paper.arxiv_id is None or paper.doi is not None:
            return paper
        return replace(paper, doi=f"10.48550/arxiv.{paper.arxiv_id}")
