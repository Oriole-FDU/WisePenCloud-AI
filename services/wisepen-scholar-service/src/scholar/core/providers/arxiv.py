"""arXiv 在线搜索适配；领域到分类的转换统一由 field_mapping 负责。"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import date

from elastic_transport import ApiError as ElasticApiError
from elastic_transport import ConnectionError as ElasticConnectionError
from elastic_transport import ConnectionTimeout as ElasticConnectionTimeout
from httpx import TransportError as HttpxTransportError
from openai import APIConnectionError, APIStatusError
from qdrant_client.http.exceptions import ApiException as QdrantApiError

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import Candidate, RrfFusion
from scholar.domain.errors import ProviderError, provider_error_message
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
)
from scholar.domain.models.paper import Paper
from scholar.domain.repositories.paper_repository import PaperRepository
from scholar.domain.repositories.paper_vector_repository import PaperVectorRepository
from scholar.ml.specter import MODEL_SIGNATURE
from scholar.core.providers.s2 import has_signals as s2_has_signals

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


def has_signals(metadata: dict) -> bool:
    """本地基础元信息只算检索底库，不算增量；仅当同步注入了 S2 字段才算富化。"""
    return s2_has_signals(metadata)


def project_metadata(paper: Paper) -> dict:
    """保留分类、发表线索与最新修订；仅当提交日期不同于顶层日期时补入。"""
    values = paper.metadata["arxiv"]
    result = {
        name: values.get(name)
        for name in ("categories", "updated_date", "journal_ref", "comments")
    }

    versions = values.get("versions")
    if versions:
        result["latest_version"] = versions[-1]["version"]

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
        paper_repository: PaperRepository,
        embedding_client: EmbeddingClient,
        *,
        vector_repository: PaperVectorRepository,
        candidate_limit: int = 200,
        num_candidates: int = 1000,
        rrf_k: int = 60,
        citation_weight: float = 0.20,
        influential_weight: float = 0.10,
    ):
        self.paper_repository = paper_repository
        self.vector_repository = vector_repository
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
                    "algorithm": "keyword-qdrant-rrf-saturation-v2",
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
        except (ElasticApiError, ElasticConnectionError, ElasticConnectionTimeout) as error:
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

        try:
            papers = await self.paper_repository.search(
                query=query,
                categories=categories,
                published_after=published_after,
                published_before=published_before,
                limit=self.candidate_limit,
            )

            # 只有编码服务拒绝查询向量时保留关键词部分结果；存储错误继续上抛。
            partial_error = None
            try:
                vector = (await self.embedding_client.embed(texts=[query]))[0]
            except (APIConnectionError, APIStatusError) as error:
                semantic = []
                partial_error = provider_error_message(error)
            else:
                semantic_ids = await self.vector_repository.search(
                    vector=vector,
                    categories=categories,
                    published_after=published_after,
                    published_before=published_before,
                    limit=self.candidate_limit,
                    num_candidates=self.num_candidates,
                )
                # ES 批量回填按输入 ID 顺序返回，保持 Qdrant 的相似度排名。
                semantic = await self.paper_repository.get_many(semantic_ids)

            # 两路召回用 RRF 融合：同一身份跨路排序靠前者得分更高。
            fused = RrfFusion(k=self.rrf_k).fuse(
                [
                    [
                        Candidate(
                            candidate_id=paper.arxiv_id,
                            payload=paper,
                            rank=rank,
                        )
                        for rank, paper in enumerate(result, 1)
                    ]
                    for result in (papers, semantic)
                ]
            )

            # 引用先验只对已召回候选做有界平滑加分；零和缺失不会抹掉相关性分数。
            def boosted_score(candidate):
                metrics = candidate.payload.metadata.get("s2", {})
                citations = metrics.get("citation_count") or 0
                influential = metrics.get("influential_citation_count") or 0
                return candidate.score * (
                    1
                    + self.citation_weight * citations / (citations + 100)
                    + self.influential_weight * influential / (influential + 10)
                )

            ranked = sorted(fused, key=boosted_score, reverse=True)
            return PaperSearchBatch(
                [
                    self._with_arxiv_doi(candidate.payload)
                    for candidate in ranked[:limit]
                ],
                partial_error,
            )
        except (
            ElasticApiError,
            ElasticConnectionError,
            ElasticConnectionTimeout,
            QdrantApiError,
            HttpxTransportError,
        ) as error:
            raise ProviderError(provider_error_message(error)) from error

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
