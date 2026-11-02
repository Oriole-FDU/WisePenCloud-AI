"""统一论文缓存：来源事实、标题索引和 Unpaywall 正负结果。"""

from __future__ import annotations

import hashlib
from copy import deepcopy

import orjson
from pydantic import TypeAdapter
from redis.asyncio import Redis

from scholar.core.providers.policies import paper_identifiers
from scholar.core.storage.redis import PAPER_CACHE_REVISION
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.paper import Paper
from scholar.domain.models.identifiers import PaperIdentifier, identities_conflict, merge_identity_groups
from scholar.utils import normalize_title
from scholar.domain.repositories.paper_cache import CachedPaperLookup

_LOOKUP = TypeAdapter(CachedPaperLookup)
_IDENTITIES = TypeAdapter(list[PaperIdentifier])

_IDENTITY_CONFLICT = "Cached lookup conflicts with the requested paper identity"


def _find_cached_record(
    source_records: dict[PaperIdentifier, CachedPaperLookup | None],
    aliases: list[PaperIdentifier],
) -> tuple[PaperIdentifier | None, CachedPaperLookup | None]:
    """在单个来源中按别名顺序查找首条有正文的缓存记录。"""
    for identity in aliases:
        record = source_records.get(identity)
        if record is not None and record.paper is not None:
            return identity, record
    return None, None


def _resolve_candidate(
    aliases: list[PaperIdentifier],
    sources: list[str],
    records: dict[str, dict[PaperIdentifier, CachedPaperLookup | None]],
) -> tuple[Paper | None, str | None]:
    """跨来源合并单篇候选论文事实：首个有正文的来源作为基础，其余补齐缺失元数据。"""
    paper = None
    error = None
    for source in sources:
        identity, record = _find_cached_record(records[source], aliases)
        if record is None:
            continue
        if identity not in record.cache_aliases:
            error = _IDENTITY_CONFLICT
        if identities_conflict(aliases, paper_identifiers(record.paper)):
            return paper, _IDENTITY_CONFLICT
        if paper is None:
            paper = deepcopy(record.paper)
        else:
            for name, facts in record.paper.metadata.items():
                paper.metadata.setdefault(name, deepcopy(facts))
    return paper, error


class RedisPaperCache:
    """同一篇论文按所有别名多键写入，这样以后用任一身份（DOI/arXiv 等）都能命中同一条事实"""

    def __init__(self, redis: Redis, namespace: str, ttl: int = 86400):
        self.redis = redis
        self.namespace = f"{PAPER_CACHE_REVISION}:{namespace}"  # 按调用方传入命名空间做来源隔离
        self.ttl = ttl

    # -- 来源记录 --

    async def get_many(
        self,
        source: str,
        ids: list[PaperIdentifier],
    ) -> list[CachedPaperLookup | None]:
        if not ids:
            return []
        values = await self.redis.mget(
            [self._key(source, identifier) for identifier in ids]
        )
        return [
            None if value is None else _LOOKUP.validate_json(value)
            for value in values
        ]

    async def put_many(
        self,
        source: str,
        entries: list[tuple[CachedPaperLookup, int]],
    ) -> None:
        if not entries:
            return
        # 同一条记录按所有别名多键写入，下次任一身份都能命中。
        async with self.redis.pipeline(transaction=True) as pipeline:
            for record, ttl in entries:
                payload = _LOOKUP.dump_json(record)
                for identifier in record.cache_aliases:
                    await pipeline.set(
                        self._key(source, identifier), payload, ex=ttl
                    )
            await pipeline.execute()
        await self.index_titles(
            [
                (record.paper.title, list(record.cache_aliases))
                for record, _ in entries
                if record.paper is not None and record.paper.title
            ]
        )

    # -- 标题索引 --

    async def index_titles(
        self,
        entries: list[tuple[str, list[PaperIdentifier]]],
    ) -> None:
        """同一标题下按「身份组」分 field 存，保留同名歧义与 arXiv↔DOI 关联。"""
        async with self.redis.pipeline(transaction=True) as pipeline:
            for title, aliases in entries:
                if not aliases:
                    continue
                key = self._title_key(title)
                # group 是稳定排序后的身份串；窄来源写入不会抹掉已确认的关联。
                group = "|".join(
                    sorted(
                        f"{identity.kind.value}:{identity.value}"
                        for identity in aliases
                    )
                )
                pipeline.hset(
                    key, mapping={group: _IDENTITIES.dump_json(aliases)}
                )
                await pipeline.expire(key, self.ttl)
            await pipeline.execute()

    async def match_titles(
        self,
        titles: list[str],
        sources: list[str],
    ) -> list[PaperLookupMatch]:
        """按标题返回候选论文；逐候选跨来源合并，缺失时继续交由本地索引回退。"""
        # 批量取每个标题的全部身份组。
        async with self.redis.pipeline(transaction=False) as pipeline:
            for title in titles:
                pipeline.hvals(self._title_key(title))
            buckets = await pipeline.execute()
        groups = [
            merge_identity_groups(
                [_IDENTITIES.validate_json(value) for value in bucket]
            )
            for bucket in buckets
        ]

        # 收集所有唯一身份，按来源一次性批量查询。
        identities = list(
            dict.fromkeys(
                identity
                for candidates in groups
                for aliases in candidates
                for identity in aliases
            )
        )
        records = {
            source: dict(
                zip(
                    identities,
                    await self.get_many(source, identities),
                    strict=True,
                )
            )
            for source in sources
        }

        # 逐候选跨来源合并：同一身份组内取首个有 paper 的来源作为基础，
        # 其余来源仅补缺失命名空间；任一候选不齐或身份冲突即判整组歧义。
        matches = []
        for candidates in groups:
            results = [
                _resolve_candidate(aliases, sources, records)
                for aliases in candidates
            ]
            papers = [paper for paper, _ in results if paper is not None]
            # 组内 error 取最后一次出现的冲突错误
            error = next((err for _, err in reversed(results) if err), None)
            # 只有身份而没有正文时继续查本地；已知同名候选不齐时也不能任选一篇。
            matches.append(
                PaperLookupMatch(
                    papers if len(papers) == len(candidates) else [],
                    error,
                )
            )

        # 命中论文若缺 PDF，用 Unpaywall 正负缓存统一补齐。
        dois = list(
            dict.fromkeys(
                paper.doi
                for match in matches
                for paper in match.papers
                if not paper.pdf_url and paper.doi
            )
        )
        pdfs = await self.get_pdf_many(dois) if dois else {}
        for match in matches:
            for paper in match.papers:
                if not paper.pdf_url and paper.doi in pdfs:
                    paper.pdf_url = pdfs[paper.doi]
        return matches

    # -- pdf 缓存 --

    async def get_pdf_many(self, dois: list[str]) -> dict[str, str | None]:
        values = await self.redis.mget([self._pdf_key(doi) for doi in dois])
        return {
            doi: orjson.loads(value)
            for doi, value in zip(dois, values, strict=True)
            if value is not None
        }

    async def put_pdf_many(
        self,
        entries: list[tuple[str, str | None, int]],
    ) -> None:
        async with self.redis.pipeline(transaction=True) as pipeline:
            for doi, url, ttl in entries:
                await pipeline.set(self._pdf_key(doi), orjson.dumps(url), ex=ttl)
            await pipeline.execute()

    # -- 失效 --

    async def invalidate(
        self,
        source: str,
        identifiers: list[PaperIdentifier],
    ) -> None:
        """来源失效只移除对应事实；残留标题索引不会阻止后续回退。"""
        if not identifiers:
            return
        aliases = list(dict.fromkeys(identifiers))
        for record in await self.get_many(source, aliases):
            if record is not None:
                aliases.extend(record.cache_aliases)
        await self.redis.delete(
            *(
                self._key(source, identity)
                for identity in dict.fromkeys(aliases)
            )
        )

    # -- key 构造 --

    def _key(self, source: str, identity: PaperIdentifier) -> str:
        return (
            f"scholar:paper:{self.namespace}:{source}"
            f":{identity.kind.value}:{identity.value}"
        )

    def _title_key(self, title: str) -> str:
        digest = hashlib.sha256(normalize_title(title).encode()).hexdigest()
        return f"scholar:paper:{self.namespace}:title:{digest}"

    def _pdf_key(self, doi: str) -> str:
        return f"scholar:paper:{self.namespace}:unpaywall:{doi}"
