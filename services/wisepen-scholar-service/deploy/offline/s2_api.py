"""每日同步使用 S2 Graph batch API；向量只在离线持久化边界消费。"""

from dataclasses import dataclass
from math import isfinite

from scholar.domain.models.identifiers import PaperIdentifier
from scholar.ml.specter import DIMENSIONS
from scholar.core.providers import ENRICHMENT_FIELDS, IDENTITY_FIELDS, S2Provider
from scholar.core.providers import S2Transport


@dataclass
class S2SyncPaper:
    """离线同步的 S2 投影，仅保留来源元数据、身份和可持久化向量。"""

    identifiers: list[PaperIdentifier]
    metadata: dict
    vector: list[float] | None


class S2SyncClient:
    """通过 Graph batch 查询 S2 元数据及同空间的 SPECTER v2 向量。"""

    def __init__(self, transport: S2Transport):
        self.transport = transport

    async def lookup(
        self, identifiers: list[PaperIdentifier]
    ) -> list[S2SyncPaper | None]:
        # 裸 embedding 默认为 v1；必须显式请求 v2，与本地 proximity 灌库处于同一空间。
        payloads = await self.transport.request(
            "POST",
            "graph/v1/paper/batch",
            params={
                "fields": ",".join(
                    (*IDENTITY_FIELDS, *ENRICHMENT_FIELDS, "embedding.specter_v2")
                )
            },
            json={
                "ids": [S2Provider.serialize_identifier(item) for item in identifiers]
            },
        )
        results = []
        for payload in payloads:
            if payload is None:
                results.append(None)
                continue
            embedding = payload.get("embedding")
            vector = None
            if embedding is not None:
                vector = embedding["vector"]
                if (
                    embedding["model"] != "specter_v2"
                    or len(vector) != DIMENSIONS
                    or not all(isfinite(value) for value in vector)
                ):
                    raise ValueError("S2 returned an invalid specter_v2 embedding")
            results.append(
                S2SyncPaper(
                    S2Provider.identifiers(payload),
                    S2Provider.metadata(payload),
                    vector,
                )
            )
        return results
