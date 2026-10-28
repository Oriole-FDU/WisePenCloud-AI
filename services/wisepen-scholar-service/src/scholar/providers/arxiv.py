"""arXiv 在线搜索适配；领域到分类的转换统一由 field_mapping 负责。"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from common.utils.ai_clients.embedding import EmbeddingClient
from common.utils.retrieval import Candidate, RrfFusion
from elastic_transport import (
    ApiError as ElasticApiError,
)
from elastic_transport import (
    ConnectionError as ElasticConnectionError,
)
from elastic_transport import (
    ConnectionTimeout as ElasticConnectionTimeout,
)
from openai import APIConnectionError, APIStatusError

from scholar.core.embedding_runtime import MODEL_SIGNATURE
from scholar.domain.errors import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import PaperIdentifier
from scholar.domain.repositories.paper_repository import PaperRepository
from scholar.providers.s2 import has_signals as s2_has_signals

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
    """判定本地仓储（arXiv/DOI）是否已含足够富化信号。

    本地基础元信息只算检索底库，不算外部增量；只有离线同步注入过 S2 字段
    才算真正富化，故直接复用 ``s2.has_signals``。
    """
    return s2_has_signals(metadata)


class ArxivProvider:
    def __init__(
        self,
        paper_repository: PaperRepository,
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
        """平台调参或模型版本变化后不复用旧排名；固定文本规则变化时递增算法版本。"""
        return hashlib.sha256(
            json.dumps(
                {
                    "algorithm": "dismax-rrf-saturation-v1",
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
            return await self.paper_repository.lookup(ids)
        except ElasticApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        except (ElasticConnectionError, ElasticConnectionTimeout) as error:
            raise ProviderError(provider_error_message(error)) from error

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
            partial_error = None
            try:
                vector = (await self.embedding_client.embed(texts=[query]))[0]
            except (APIConnectionError, APIStatusError) as error:
                # 只允许已知编码服务边界失败降级；无效响应、程序错误和取消直接传播。
                semantic = []
                partial_error = provider_error_message(error)
            else:
                semantic = await self.paper_repository.search_vector(
                    vector=vector,
                    categories=categories,
                    published_after=published_after,
                    published_before=published_before,
                    limit=self.candidate_limit,
                    num_candidates=self.num_candidates,
                )
            fused = RrfFusion(k=self.rrf_k).fuse(
                [
                    [
                        Candidate(candidate_id=paper.arxiv_id, payload=paper, rank=rank)
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
                [candidate.payload.project() for candidate in ranked[:limit]],
                partial_error,
            )
        except ElasticApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        except (ElasticConnectionError, ElasticConnectionTimeout) as error:
            raise ProviderError(provider_error_message(error)) from error
