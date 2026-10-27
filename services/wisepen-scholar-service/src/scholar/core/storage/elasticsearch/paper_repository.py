"""ES 是可重建的完整论文投影，HTTP 从不访问 ETL 的 DuckDB。"""

from datetime import date

import orjson
from common.logger import warn
from elasticsearch import AsyncElasticsearch

from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import (
    PaperLookupMatch,
)

ARXIV_MAPPING = {
    "dynamic": "strict",
    "properties": {
        **{
            field: {"type": "keyword"}
            for field in (
                "arxiv_id",
                "raw_id",
                "doi",
                "categories",
                "license",
                "url",
                "pdf_url",
            )
        },
        **{
            # 原字段保留未词干的词项；.en 去停用词并归并英语词形，用于概念召回。
            field: {
                "type": "text",
                "fields": {"en": {"type": "text", "analyzer": "english"}},
            }
            for field in ("title", "abstract")
        },
        **{field: {"type": "text"} for field in ("authors", "journal_ref", "comments")},
        "submitted_date": {"type": "date"},
        "updated_date": {"type": "date"},
        "versions": {"type": "object", "enabled": False},
        "metadata": {
            "properties": {
                "s2": {
                    "properties": {
                        "corpus_id": {"type": "keyword"},
                        "citation_count": {"type": "long"},
                        "influential_citation_count": {"type": "long"},
                        "is_open_access": {"type": "boolean"},
                        "tldr": {
                            "properties": {
                                "model": {"type": "keyword"},
                                "text": {
                                    "type": "text",
                                    "fields": {
                                        "en": {"type": "text", "analyzer": "english"}
                                    },
                                },
                            }
                        },
                        "release": {"type": "keyword"},
                        "paper_source": {"type": "object", "enabled": False},
                        "tldr_source": {"type": "object", "enabled": False},
                    }
                }
            }
        },
    },
}


def create_elasticsearch(
    uris: str, username: str, password: str, timeout: float
) -> AsyncElasticsearch:
    import json

    hosts = (
        json.loads(uris)
        if uris.strip().startswith("[")
        else [v.strip() for v in uris.split(",")]
    )
    return AsyncElasticsearch(
        hosts,
        basic_auth=(username, password),
        request_timeout=timeout,
        max_retries=0,
        http_compress=True,
    )


class ElasticsearchPaperRepository:
    """本地全文召回和精确身份查询；不包含来源路由和缓存策略。"""

    def __init__(self, es: AsyncElasticsearch, alias: str):
        self.es, self.alias = es, alias

    async def search(
        self,
        *,
        query: str,
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
    ) -> list[Paper]:
        filters = []
        if categories:
            filters.append(
                {
                    "bool": {
                        "should": [
                            {"wildcard": {"categories": value}}
                            if "*" in value
                            else {"term": {"categories": value}}
                            for value in categories
                        ],
                        "minimum_should_match": 1,
                    }
                }
            )
        bounds = {}
        if published_after:
            bounds["gte"] = published_after.isoformat()
        if published_before:
            bounds["lte"] = published_before.isoformat()
        if bounds:
            filters.append({"range": {"submitted_date": bounds}})
        response = await self.es.search(
            index=self.alias,
            size=limit,
            track_total_hits=False,
            query={
                "bool": {
                    "must": [
                        {
                            # BM25F 近似按词跨字段匹配，允许标题与摘要共同满足命中门槛。
                            # 三个字段使用相同的 english analyzer，门槛不计英语停用词。
                            "combined_fields": {
                                "query": query,
                                "fields": [
                                    "title.en^3",
                                    "metadata.s2.tldr.text.en^2",
                                    "abstract.en",
                                ],
                                "minimum_should_match": "2<70%",
                            }
                        }
                    ],
                    # 原词形与短语只调整已召回论文的分数，不扩大召回或引入作者名命中。
                    "should": [
                        {
                            "multi_match": {
                                "query": query,
                                "type": "most_fields",
                                "fields": [
                                    "title",
                                    "metadata.s2.tldr.text^0.5",
                                    "abstract^0.3",
                                ],
                            }
                        },
                        {"match_phrase": {"title.en": {"query": query, "boost": 2}}},
                        {
                            "match_phrase": {
                                "abstract.en": {
                                    "query": query,
                                    "slop": 2,
                                    "boost": 0.5,
                                }
                            }
                        },
                    ],
                    "filter": filters,
                }
            },
        )
        return [self._to_paper(hit["_source"]) for hit in response["hits"]["hits"]]

    async def lookup(
        self, identifiers: list[PaperIdentifier]
    ) -> list[PaperLookupMatch]:
        if not identifiers:
            return []
        searches = []
        for identifier in identifiers:
            field = "arxiv_id" if identifier.kind == IdentifierKind.ARXIV else "doi"
            searches.extend(
                [
                    {"index": self.alias},
                    {"size": 2, "query": {"term": {field: identifier.value}}},
                ]
            )
        response = await self.es.msearch(searches=searches)
        results = []
        for result in response["responses"]:
            if "error" in result:
                warn(
                    "paper lookup source failed.", source="arxiv", error=result["error"]
                )
                results.append(
                    PaperLookupMatch(
                        papers=[], error=orjson.dumps(result["error"]).decode()
                    )
                )
                continue
            # 两个结果足以证明身份歧义；不能任意取其中一个 DOI 候选。
            results.append(
                PaperLookupMatch(
                    papers=[
                        self._to_paper(hit["_source"]) for hit in result["hits"]["hits"]
                    ]
                )
            )
        return results

    @staticmethod
    def _to_paper(document: dict) -> Paper:
        # ES 日期字符串只在读取边界转换；应用层消费受控的论文事实。
        submitted = date.fromisoformat(document["submitted_date"])
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
            # 供应商附加信息按来源命名空间保留，不扩散为领域级强类型依赖。
            metadata={
                **document.get("metadata", {}),
                "arxiv": {
                    name: document[name]
                    for name in (
                        "raw_id",
                        "categories",
                        "submitted_date",
                        "updated_date",
                        "versions",
                        "journal_ref",
                        "comments",
                        "license",
                    )
                },
            },
        )
