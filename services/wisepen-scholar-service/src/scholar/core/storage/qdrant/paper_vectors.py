"""Scholar 的 arXiv 稠密向量投影及在线候选召回。"""

from __future__ import annotations

from datetime import UTC, date, datetime, time
from uuid import NAMESPACE_URL, uuid5

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import (
    DatetimeRange,
    Distance,
    FieldCondition,
    Filter,
    HnswConfigDiff,
    MatchValue,
    PayloadSchemaType,
    SearchParams,
    VectorParams,
)

from scholar.ml.specter import DIMENSIONS, MODEL_SIGNATURE

COLLECTION = f"wisepen-scholar-arxiv-{MODEL_SIGNATURE[:16]}"


def point_id(arxiv_id: str) -> str:
    """为同一 arXiv 身份生成稳定的 Qdrant UUID。"""
    return str(uuid5(NAMESPACE_URL, f"arxiv:{arxiv_id}"))


def point_payload(document: dict) -> dict:
    """仅投影在线身份回填与预过滤所需的 arXiv 字段。"""
    categories = document["categories"]
    submitted = date.fromisoformat(document["submitted_date"])
    groups = sorted({category.partition(".")[0] for category in categories})
    return {
        "arxiv_id": document["arxiv_id"],
        "categories": categories,
        "category_groups": groups,
        "submitted_date": f"{submitted.isoformat()}T00:00:00Z",
    }


async def ensure_collection(
    client: AsyncQdrantClient, collection: str = COLLECTION
) -> None:
    """创建向量集合与过滤索引，或验证已有集合的维度和距离合同。"""
    if await client.collection_exists(collection_name=collection):
        info = await client.get_collection(collection_name=collection)
        vectors = info.config.params.vectors
        if (
            not isinstance(vectors, VectorParams)
            or vectors.size != DIMENSIONS
            or vectors.distance != Distance.EUCLID
        ):
            raise ValueError(
                f"Qdrant collection {collection!r} must use a {DIMENSIONS}-dimensional Euclid vector"
            )
    else:
        await client.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(
                size=DIMENSIONS,
                distance=Distance.EUCLID,
                on_disk=True,
            ),
            hnsw_config=HnswConfigDiff(on_disk=True),
        )

    for field_name, field_schema in (
        ("categories", PayloadSchemaType.KEYWORD),
        ("category_groups", PayloadSchemaType.KEYWORD),
        ("submitted_date", PayloadSchemaType.DATETIME),
    ):
        await client.create_payload_index(
            collection_name=collection,
            field_name=field_name,
            field_schema=field_schema,
        )


class QdrantPaperVectorRepository:
    """从 Qdrant 按语义召回 arXiv 身份，论文事实仍由 Elasticsearch 持有。"""

    def __init__(
        self,
        client: AsyncQdrantClient,
        collection: str = COLLECTION,
    ):
        self.client = client
        self.collection = collection

    async def search(
        self,
        *,
        vector: list[float],
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
        num_candidates: int,
    ) -> list[str]:
        """按语义、学科和含边界的提交日期预过滤，并返回 Qdrant 排序的身份。"""
        category_conditions = [
            FieldCondition(
                key="category_groups" if "*" in category else "categories",
                match=MatchValue(
                    value=category.partition(".")[0]
                    if "*" in category
                    else category
                ),
            )
            for category in categories
        ]
        date_condition = None
        if published_after is not None or published_before is not None:
            date_condition = FieldCondition(
                key="submitted_date",
                range=DatetimeRange(
                    gte=(
                        datetime.combine(published_after, time.min, UTC)
                        if published_after is not None
                        else None
                    ),
                    lte=(
                        datetime.combine(published_before, time.min, UTC)
                        if published_before is not None
                        else None
                    ),
                ),
            )
        has_filter = bool(category_conditions) or date_condition is not None

        response = await self.client.query_points(
            collection_name=self.collection,
            query=vector,
            query_filter=(
                Filter(
                    must=[date_condition] if date_condition is not None else None,
                    should=category_conditions or None,
                )
                if has_filter
                else None
            ),
            limit=limit,
            search_params=SearchParams(hnsw_ef=num_candidates),
            with_payload=["arxiv_id"],
            with_vectors=False,
        )
        return [point.payload["arxiv_id"] for point in response.points]
