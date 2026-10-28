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
from scholar.domain.repositories.paper_lookup_cache import CachedPaperLookup

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


def has_signals(metadata: dict) -> bool:
    """判定 S2 元数据是否含有效富化信号。

    三类价值维度取 OR，覆盖不同时效与学科的论文：

    - 计量指标：``citation_count`` / ``influential_citation_count`` /
      ``is_open_access`` 任一非 None（新论文可能尚无 TLDR，但已有引用统计）。
    - 速读价值：``tldr.text`` 非空（引用为 0 的预印本也可能已有 LLM 摘要）。
    - 科研资产：``open_access_pdf`` 或 ``bibtex`` 存在（直接满足阅读/引用刚需）。
    """
    values = metadata.get("s2", {})
    tldr = values.get("tldr")
    return (
            any(
                values.get(key) is not None
                for key in (
                    "citation_count",
                    "influential_citation_count",
                    "is_open_access",
                )
            )
            or bool(tldr and tldr.get("text"))
            or bool(values.get("open_access_pdf") or values.get("bibtex"))
    )


def aliases(metadata: dict) -> list[PaperIdentifier]:
    """从 S2 响应中提取 paper_id 与 corpus_id 作为别名。

    别名用于：多 Key 共享缓存（同一份数据同时写入 ``s2:<id>``、
    ``corpusid:<id>``）、跨源身份接力，以及比对 known 与 actual 别名以
    拦截模糊匹配造成的串号。
    """
    values = metadata.get("s2", {})
    aliases = []
    if values.get("paper_id"):
        aliases.append(parse_identifier(f"s2:{values['paper_id']}"))
    if values.get("corpus_id") is not None:
        aliases.append(parse_identifier(f"corpusid:{values['corpus_id']}"))
    return aliases


def short_lived(record: CachedPaperLookup) -> bool:
    """判定 S2 lookup 缓存是否暂态或缺少关键信号（是则用短 TTL）。

    与 ``has_signals`` 的「宽进」互补：这里「严出」，任一残缺即降级为短 TTL，
    避免负结果或半成品长期占缓存。触发条件：

    - 已确认未找到（``paper`` 为空）；
    - ``citation_count`` 或 ``influential_citation_count`` 尚未出炉；
    - ``tldr`` 缺失或其 ``text`` 为空（LLM 摘要仍在生成）。
    """
    if record.paper is None:
        return True
    values = record.paper.metadata.get("s2", {})
    tldr = values.get("tldr")
    return (
            values.get("citation_count") is None
            or values.get("influential_citation_count") is None
            or not tldr
            or not tldr.get("text")
    )


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
