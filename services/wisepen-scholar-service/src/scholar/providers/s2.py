"""Semantic Scholar 批量论文查询适配。"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from datetime import UTC, date, datetime
from uuid import uuid4

import httpx
from redis.asyncio import Redis
from redis.exceptions import RedisError

from scholar.domain.errors import (
    ProviderBusy,
    ProviderError,
    provider_error_message,
)
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import (
    IdentifierKind,
    PaperIdentifier,
    normalize_arxiv,
    normalize_doi,
    parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource

# S2 排队观测：记录当前请求在 FIFO 队列前等待的项数；不属于论文事实，不进入论文缓存。
s2_queued_ahead: ContextVar[int | None] = ContextVar("s2_queued_ahead", default=None)

# 使用 Redis 时钟和原子脚本，避免实例时钟差异改变 FIFO 或发送间隔。
# 租约只覆盖等待期；崩溃进程的占位由后续入队/轮询清理，不需要后台清扫器。
_PRUNE = """
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
for _, member in ipairs(redis.call('ZRANGEBYSCORE', KEYS[2], '-inf', now)) do
    redis.call('ZREM', KEYS[1], member)
    redis.call('ZREM', KEYS[2], member)
end
"""
_ADMIT = (
    _PRUNE
    + """
local ahead = redis.call('ZCARD', KEYS[1])
if tonumber(ARGV[2]) >= 0 and ahead > tonumber(ARGV[2]) then
    return {0, ahead}
end
local sequence = redis.call('INCR', KEYS[3])
redis.call('ZADD', KEYS[1], sequence, ARGV[1])
redis.call('ZADD', KEYS[2], now + 15000, ARGV[1])
return {1, ahead}
"""
)
_PERMIT = (
    _PRUNE
    + """
if not redis.call('ZSCORE', KEYS[1], ARGV[1]) then return -1 end
redis.call('ZADD', KEYS[2], now + 15000, ARGV[1])
local head = redis.call('ZRANGE', KEYS[1], 0, 0)[1]
if head ~= ARGV[1] then return 250 end
local previous = tonumber(redis.call('GET', KEYS[3]) or '0')
local delay = previous + 1000 - now
if delay > 0 then return delay end
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
redis.call('SET', KEYS[3], now, 'PX', 2000)
return 0
"""
)
_REMOVE = """
redis.call('ZREM', KEYS[1], ARGV[1])
redis.call('ZREM', KEYS[2], ARGV[1])
return 0
"""


class S2Transport:
    def __init__(
        self,
        http: httpx.AsyncClient,
        redis: Redis,
        api_key: str,
        *,
        max_queued_ahead: int | None = None,
        namespace: str = "scholar:s2",
    ):
        self.http, self.redis, self._api_key = http, redis, api_key
        # None 用于离线调用：参与同一配额，但不应用在线拥堵准入阈值。
        self.max_queued_ahead = max_queued_ahead
        self._queue = f"{namespace}:queue"
        self._leases = f"{namespace}:leases"
        self._sequence = f"{namespace}:sequence"
        # 沿用旧发送许可键，正在运行的旧版离线任务仍共享 1 RPS。
        self._permit = f"{namespace}:send-permit"
        self.http.headers.pop("User-Agent", None)

    async def request(self, method: str, path: str, **kwargs):
        member = uuid4().hex
        try:
            accepted, ahead = await self.redis.eval(
                _ADMIT,
                3,
                self._queue,
                self._leases,
                self._sequence,
                member,
                -1 if self.max_queued_ahead is None else self.max_queued_ahead,
            )
            s2_queued_ahead.set(ahead)
            if not accepted:
                raise ProviderBusy(ahead, self.max_queued_ahead)
            while True:
                delay = await self.redis.eval(
                    _PERMIT,
                    3,
                    self._queue,
                    self._leases,
                    self._permit,
                    member,
                )
                if delay < 0:
                    raise ProviderError(
                        "S2 waiting lease expired before send permission"
                    )
                if delay == 0:
                    break
                await asyncio.sleep(delay / 1000)
        finally:
            # 取消、超时、Redis 故障均不得留下可绕过配额的发送路径。
            # Redis 暂时不可用时由租约回收，清理错误仍向调用者传播。
            await self.redis.eval(_REMOVE, 2, self._queue, self._leases, member)

        request = self.http.build_request(
            method,
            f"https://api.semanticscholar.org/{path}",
            headers={"x-api-key": self._api_key},
            **kwargs,
        )
        response = await self.http.send(request)
        response.raise_for_status()
        return response.json()


IDENTITY_FIELDS = ("paperId", "corpusId", "externalIds", "url", "title")
ENRICHMENT_FIELDS = (
    "tldr",
    "citationCount",
    "influentialCitationCount",
    "isOpenAccess",
    "openAccessPdf",
    "citationStyles",
)
DETAIL_FIELDS = ("abstract", "authors", "year", "publicationDate", "venue")


class S2Provider:
    def __init__(self, transport: S2Transport):
        self.transport = transport

    async def lookup(
        self,
        ids: list[PaperIdentifier],
        *,
        api_key: str | None = None,
    ) -> list[PaperLookupMatch]:
        fields = IDENTITY_FIELDS + ENRICHMENT_FIELDS + DETAIL_FIELDS
        try:
            response = await self.transport.request(
                "POST",
                "graph/v1/paper/batch",
                params={"fields": ",".join(fields)},
                json={
                    "ids": [self.serialize_identifier(identifier) for identifier in ids]
                },
            )
        except (httpx.HTTPError, RedisError) as error:
            raise ProviderError(provider_error_message(error)) from error
        results = []
        for _, item in zip(ids, response, strict=True):
            if item is None:
                results.append(PaperLookupMatch([]))
                continue
            metadata = self.metadata(item)
            results.append(PaperLookupMatch([self._paper(item, metadata)]))
        return results

    @staticmethod
    def serialize_identifier(identifier: PaperIdentifier) -> str:
        if identifier.kind == IdentifierKind.ARXIV:
            return f"arxiv:{identifier.value}"
        if identifier.kind == IdentifierKind.DOI:
            return f"doi:{identifier.value}"
        if identifier.kind == IdentifierKind.CORPUS_ID:
            return f"CorpusId:{identifier.value}"
        if identifier.kind == IdentifierKind.S2:
            return identifier.value
        raise ValueError(f"S2 does not accept {identifier.kind.value} identifiers")

    @staticmethod
    def metadata(payload: dict) -> dict:
        tldr = payload.get("tldr")
        pdf = payload.get("openAccessPdf")
        citation_styles = payload.get("citationStyles")
        metadata = {
            "s2": {
                "corpus_id": None
                if payload.get("corpusId") is None
                else str(payload["corpusId"]),
                "paper_id": payload.get("paperId"),
                "citation_count": payload.get("citationCount"),
                "influential_citation_count": payload.get("influentialCitationCount"),
                "is_open_access": payload.get("isOpenAccess"),
                "tldr": None
                if tldr is None
                else {"model": tldr.get("model"), "text": tldr.get("text")},
                "open_access_pdf": None if pdf is None else pdf.get("url") or None,
                "bibtex": None
                if citation_styles is None
                else citation_styles.get("bibtex"),
            }
        }
        # 抓取时间和摘要模型属于缓存事实，API 出口投影时才裁剪。
        metadata["s2"]["observed_at"] = datetime.now(UTC).isoformat()
        if "venue" in payload:
            metadata["s2"]["venue"] = payload["venue"]
        return metadata

    @staticmethod
    def identifiers(payload: dict) -> list[PaperIdentifier]:
        identifiers = []
        if payload.get("paperId"):
            identifiers.append(parse_identifier(f"s2:{payload['paperId']}"))
        if payload.get("corpusId") is not None:
            identifiers.append(parse_identifier(f"corpusid:{payload['corpusId']}"))
        external_ids = payload.get("externalIds") or {}
        if external_ids.get("ArXiv"):
            identifiers.append(parse_identifier(external_ids["ArXiv"]))
        if external_ids.get("DOI"):
            identifiers.append(parse_identifier(external_ids["DOI"]))
        return list(dict.fromkeys(identifiers))

    @staticmethod
    def _paper(payload: dict, metadata: dict) -> Paper:
        external_ids = payload.get("externalIds") or {}
        publication_date = payload.get("publicationDate")
        authors = payload.get("authors")
        return Paper(
            arxiv_id=None
            if not external_ids.get("ArXiv")
            else normalize_arxiv(external_ids["ArXiv"]),
            doi=None
            if not external_ids.get("DOI")
            else normalize_doi(external_ids["DOI"]),
            title=payload.get("title"),
            abstract=payload.get("abstract"),
            authors=[] if authors is None else [author["name"] for author in authors],
            publication_date=None
            if publication_date is None
            else date.fromisoformat(publication_date),
            year=payload.get("year"),
            url=payload["url"],
            pdf_url=metadata["s2"]["open_access_pdf"],
            source=PaperSource.S2,
            metadata=metadata,
        )
