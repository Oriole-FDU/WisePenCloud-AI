"""S2 论文查询、身份映射、来源信号与公开元数据投影。"""

from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
from redis.exceptions import RedisError

from scholar.domain.errors import ProviderError, provider_error_message
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import (
    IdentifierKind, PaperIdentifier, normalize_arxiv, normalize_doi, parse_identifier,
)
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_lookup_cache import CachedPaperLookup
from .transport import S2Transport

IDENTITY_FIELDS = ("paperId", "corpusId", "externalIds", "url", "title")
ENRICHMENT_FIELDS = (
    "tldr",
    "citationCount",
    "influentialCitationCount",
    "openAccessPdf",
)
DETAIL_FIELDS = ("abstract", "authors", "year", "publicationDate", "venue")


def has_signals(metadata: dict) -> bool:
    """判定 S2 元数据是否含有效富化信号。

    三类价值维度取 OR，覆盖不同时效与学科的论文：

    - 计量指标：``citation_count`` / ``influential_citation_count`` 任一非 None。
    - 速读价值：``tldr.text`` 非空（引用为 0 的预印本也可能已有 LLM 摘要）。
    - 阅读入口：``open_access_pdf`` 存在。
    """
    values = metadata.get("s2", {})
    tldr = values.get("tldr")
    return (
        any(
            values.get(key) is not None
            for key in (
                "citation_count",
                "influential_citation_count",
            )
        )
        or bool(tldr and tldr.get("text"))
        or bool(values.get("open_access_pdf"))
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


def pdf_url(paper: Paper) -> str | None:
    return paper.metadata.get("s2", {}).get("open_access_pdf")


def project_metadata(paper: Paper) -> dict:
    """保留速读和引用影响；PDF 字段只有提供独立阅读入口时才额外输出。"""
    values = paper.metadata["s2"]
    result = {
        name: values.get(name)
        for name in (
            "corpus_id",
            "citation_count",
            "influential_citation_count",
            "venue",
        )
    }
    tldr = values.get("tldr")
    result["tldr"] = tldr.get("text") if tldr is not None else None
    if values.get("open_access_pdf") not in (paper.url, paper.pdf_url):
        result["open_access_pdf"] = values.get("open_access_pdf")
    return result


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
                api_key=api_key,
                params={"fields": ",".join(fields)},
                json={
                    "ids": [
                        self.serialize_identifier(identifier)
                        for identifier in ids
                    ]
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
        metadata = {
            "s2": {
                "corpus_id": (
                    None
                    if payload.get("corpusId") is None
                    else str(payload["corpusId"])
                ),
                "paper_id": payload.get("paperId"),
                "citation_count": payload.get("citationCount"),
                "influential_citation_count": payload.get(
                    "influentialCitationCount"
                ),
                "tldr": (
                    None
                    if tldr is None
                    else {"model": tldr.get("model"), "text": tldr.get("text")}
                ),
                "open_access_pdf": None if pdf is None else pdf.get("url") or None,
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
            arxiv_id=(
                None
                if not external_ids.get("ArXiv")
                else normalize_arxiv(external_ids["ArXiv"])
            ),
            doi=(
                None
                if not external_ids.get("DOI")
                else normalize_doi(external_ids["DOI"])
            ),
            title=payload.get("title"),
            abstract=payload.get("abstract"),
            authors=[] if authors is None else [author["name"] for author in authors],
            publication_date=(
                None
                if publication_date is None
                else date.fromisoformat(publication_date)
            ),
            year=payload.get("year"),
            url=payload["url"],
            pdf_url=metadata["s2"]["open_access_pdf"],
            source=PaperSource.S2,
            metadata=metadata,
        )
