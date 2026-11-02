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
from .transport import S2Transport

# paperId 由 S2 自动返回；离线同步只需要身份，不请求在线详情字段。
IDENTITY_FIELDS = ("corpusId", "externalIds")
ENRICHMENT_FIELDS = (
    "tldr",
    "citationCount",
    "influentialCitationCount",
    "openAccessPdf",
)
DETAIL_FIELDS = ("url", "title", "abstract", "authors", "year", "publicationDate", "venue")


def aliases(metadata: dict) -> list[PaperIdentifier]:
    """从 S2 来源事实中提取平台支持的身份，归一化后保序去重。

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
    external_ids = values.get("external_ids") or {}
    # 其余外部身份不属于 IdentifierKind，不能落入 parse_identifier 的 arXiv 兜底。
    for key, prefix in (("ArXiv", "arxiv"), ("DOI", "doi"), ("CorpusId", "corpusid")):
        if external_ids.get(key) is not None and external_ids[key] != "":
            aliases.append(parse_identifier(f"{prefix}:{external_ids[key]}"))
    return list(dict.fromkeys(aliases))


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
        except TimeoutError as error:
            # 队列预算属于已知来源失败，不能绕过 LookupRunner 的富化隔离。
            raise ProviderError(
                f"S2 queue/HTTP request timed out (budget {self.transport.request_timeout:g}s): "
                f"{str(error) or type(error).__name__}"
            ) from error
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
                # 只保存有别名消费方的外部身份，不透传整份 externalIds。
                "external_ids": {
                    key: value
                    for key, value in (payload.get("externalIds") or {}).items()
                    if key in ("ArXiv", "DOI", "CorpusId") and value is not None and value != ""
                },
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
