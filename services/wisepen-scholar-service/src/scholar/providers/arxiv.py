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
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.interfaces.provider_error import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.models.fields import AcademicField
from scholar.domain.models.identifiers import PaperIdentifier, parse_identifier
from scholar.domain.repositories.paper_repository import PaperRepository
from scholar.providers.field_mapping import ARXIV_CATEGORIES


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

    async def enrich(
        self,
        identifiers: list[PaperIdentifier],
        *,
        include_details: bool = False,
        api_key: str | None = None,
    ) -> list[PaperEnrichment | None]:
        """按 arXiv/DOI 精确身份批量投影仓储已有事实，不触发外部请求。"""
        try:
            matches = await self.paper_repository.lookup(identifiers)
        except ElasticApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        except (ElasticConnectionError, ElasticConnectionTimeout) as error:
            raise ProviderError(provider_error_message(error)) from error

        enrichments = []
        for identifier, match in zip(identifiers, matches, strict=True):
            if match.error is not None:
                raise ProviderError(match.error)
            if len(match.papers) != 1:
                enrichments.append(None)
                continue

            paper = match.papers[0]
            aliases = [identifier]
            if paper.arxiv_id is not None:
                aliases.append(parse_identifier(paper.arxiv_id))
            if paper.doi is not None:
                aliases.append(parse_identifier(paper.doi))
            enrichments.append(
                PaperEnrichment(
                    identifiers=list(dict.fromkeys(aliases)),
                    metadata=paper.metadata,
                    paper=paper if include_details else None,
                )
            )
        return enrichments

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
                [candidate.payload for candidate in ranked[:limit]], partial_error
            )
        except ElasticApiError as error:
            raise ProviderError(provider_error_message(error)) from error
        except (ElasticConnectionError, ElasticConnectionTimeout) as error:
            raise ProviderError(provider_error_message(error)) from error
