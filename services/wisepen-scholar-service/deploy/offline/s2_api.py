"""每日同步使用 S2 Graph batch API；向量只在离线持久化边界消费。"""

from dataclasses import dataclass
from math import isfinite
import httpx

from scholar.domain.models.identifiers import PaperIdentifier, parse_identifier
from scholar.ml.specter import DIMENSIONS
from scholar.core.providers.s2.provider import ENRICHMENT_FIELDS, IDENTITY_FIELDS, S2Provider
from scholar.core.providers.s2 import S2Transport


def _safe_identifiers(payload: dict) -> list[PaperIdentifier]:
    """安全解析 S2 返回的外部标识，过滤脏数据（如格式不合规的 DOI），避免阻断主流程。"""
    identifiers = []
    if payload.get("paperId"):
        try:
            identifiers.append(parse_identifier(f"s2:{payload['paperId']}"))
        except ValueError:
            pass
    if payload.get("corpusId") is not None:
        try:
            identifiers.append(parse_identifier(f"corpusid:{payload['corpusId']}"))
        except ValueError:
            pass
    external_ids = payload.get("externalIds") or {}
    if external_ids.get("ArXiv"):
        try:
            identifiers.append(parse_identifier(external_ids["ArXiv"]))
        except ValueError:
            pass
    if external_ids.get("DOI"):
        try:
            identifiers.append(parse_identifier(external_ids["DOI"]))
        except ValueError:
            pass
    return list(dict.fromkeys(identifiers))


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
        if not identifiers:
            return []

        # 裸 embedding 默认为 v1；必须显式请求 v2，与本地 proximity 灌库处于同一空间。
        try:
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
        except httpx.HTTPStatusError as err:
            err_text = err.response.text.lower()
            # S2 核心机制：当整批论文在 S2 中均未收录时，S2 不返回全 null，而是返回 HTTP 400: {"error": "No valid paper ids given"}
            if err.response.status_code == 400 and "no valid paper ids" in err_text:
                return [None] * len(identifiers)
            # 若是其他 400 格式错误，二分定位隔离问题样本
            if err.response.status_code == 400 and len(identifiers) > 1:
                mid = len(identifiers) // 2
                left = await self.lookup(identifiers[:mid])
                right = await self.lookup(identifiers[mid:])
                return left + right
            elif err.response.status_code == 400 and len(identifiers) == 1:
                return [None]
            raise

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
                    _safe_identifiers(payload),
                    S2Provider.metadata(payload),
                    vector,
                )
            )
        return results
