"""Scholar 的 Qdrant 混合检索与批量身份查询仓储实现。"""

from __future__ import annotations

from datetime import date

from qdrant_client import AsyncQdrantClient, models
from qdrant_client.http.exceptions import ApiException

from scholar.domain.errors import ProviderError, provider_error_message
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.local_paper_repository import (
    LocalPaperRepository,
    PaperCandidate,
)

BM25_FIELDS = {
    "bm25_title": "title",
    "bm25_tldr": "metadata.s2.tldr.text",
    "bm25_abstract": "abstract",
}
BM25_WEIGHTS = [3.0, 2.0, 1.0]
# 写入与查询固定同一文本处理合同；IDF 随集合更新，文档权重不预乘 IDF。
BM25_OPTIONS = {"language": "english", "k": 1.2, "b": 0.75, "avg_len": 256.0}


def to_paper(document: dict) -> Paper:
    submitted = date.fromisoformat(document["submitted_date"][:10])
    return Paper(
        arxiv_id=document["arxiv_id"],
        doi=document["doi"],
        title=document["title"],
        abstract=document["abstract"],
        authors=document["authors"],
        publication_date=submitted,
        year=submitted.year,
        url=document["url"],
        pdf_url=document["pdf_url"],
        source=PaperSource.ARXIV,
        metadata={
            **document.get("metadata", {}),
            "arxiv": {
                **{
                    name: document[name]
                    for name in (
                        "raw_id",
                        "categories",
                        "updated_date",
                        "versions",
                        "journal_ref",
                        "comments",
                        "license",
                    )
                },
                "submitted_date": submitted.isoformat(),
            },
        },
    )


class QdrantPaperRepository:
    """基于 Qdrant 的本地论文混合检索仓储，实现 LocalPaperRepository。"""

    def __init__(self, client: AsyncQdrantClient, collection: str):
        self.client = client
        self.collection = collection

    async def search(
        self,
        *,
        query: str,
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
        vector: list[float] | None = None,
        num_candidates: int = 1000,
        rrf_k: int = 60,
    ) -> list[PaperCandidate]:
        conditions = []
        if categories:
            conditions.append(
                models.Filter(
                    should=[
                        models.FieldCondition(
                            key="category_groups" if value.endswith(".*") else "categories",
                            match=models.MatchValue(
                                value=value.partition(".")[0]
                                if value.endswith(".*")
                                else value
                            ),
                        )
                        for value in categories
                    ]
                )
            )
        if published_after is not None or published_before is not None:
            conditions.append(
                models.FieldCondition(
                    key="submitted_date",
                    range=models.DatetimeRange(
                        gte=f"{published_after}T00:00:00Z" if published_after else None,
                        lte=f"{published_before}T00:00:00Z" if published_before else None,
                    ),
                )
            )
        filters = models.Filter(must=conditions) if conditions else None
        # 先在关键词通道内按标题/TLDR/摘要权重融合，再与稠密通道等权融合；
        # 避免三个文本分支在外层 RRF 中无意把关键词总权重放大三倍。
        lexical = [
            models.Prefetch(
                query=models.Document(
                    text=query, model="Qdrant/bm25", options=BM25_OPTIONS
                ),
                using=name,
                filter=filters,
                limit=limit,
            )
            for name in BM25_FIELDS
        ]
        fusion = models.RrfQuery(rrf=models.Rrf(k=rrf_k, weights=BM25_WEIGHTS))
        prefetch = lexical
        if vector is not None:
            prefetch = [
                models.Prefetch(prefetch=lexical, query=fusion, limit=limit),
                models.Prefetch(
                    query=vector,
                    using="dense",
                    filter=filters,
                    limit=limit,
                    params=models.SearchParams(hnsw_ef=num_candidates),
                ),
            ]
            fusion = models.RrfQuery(rrf=models.Rrf(k=rrf_k))
        response = await self.client.query_points(
            self.collection,
            prefetch=prefetch,
            query=fusion,
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )
        return [
            PaperCandidate(to_paper(point.payload), point.score)
            for point in response.points
        ]

    async def lookup(
        self, identifiers: list[PaperIdentifier]
    ) -> list[PaperLookupMatch]:
        if not identifiers:
            return []
        try:
            results = await self.client.query_batch_points(
                self.collection,
                requests=[
                    models.QueryRequest(
                        filter=models.Filter(
                            must=[
                                models.FieldCondition(
                                    key="arxiv_id"
                                    if item.kind == IdentifierKind.ARXIV
                                    else "doi",
                                    match=models.MatchValue(value=item.value),
                                )
                            ]
                        ),
                        limit=2,
                        with_payload=True,
                        with_vector=False,
                    )
                    for item in identifiers
                ],
            )
        except ApiException as exc:
            raise ProviderError(
                f"qdrant.lookup: {provider_error_message(exc)}"
            ) from exc
        return [
            PaperLookupMatch([to_paper(point.payload) for point in batch.points])
            for batch in results
        ]
