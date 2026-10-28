"""ES 是可重建的完整论文投影，HTTP 从不访问 ETL 的 DuckDB。"""

from __future__ import annotations

from datetime import date

import orjson
from common.logger import warn
from elastic_transport import ApiError, ConnectionError, ConnectionTimeout
from elasticsearch import AsyncElasticsearch

from scholar.core.embedding_runtime import DIMENSIONS, MODEL_SIGNATURE
from scholar.domain.interfaces.provider_error import (
    ProviderError,
    provider_error_message,
)
from scholar.domain.models.identifiers import IdentifierKind, PaperIdentifier
from scholar.domain.models.paper import Paper, PaperSource
from scholar.domain.repositories.paper_repository import (
    PaperLookupMatch,
)

ARXIV_MAPPING = {
    "dynamic": "strict",
    "_meta": {"embedding_signature": MODEL_SIGNATURE},
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

# 标题正文在两个 arXiv 写入入口均已归并空白；keyword normalizer 负责大小写。
ARXIV_MAPPING["properties"]["title"]["fields"]["exact"] = {
    "type": "keyword",
    "normalizer": "scholar_title",
    "ignore_above": 8191,
}
ARXIV_MAPPING["properties"]["embedding"] = {
    "type": "dense_vector",
    "dims": DIMENSIONS,
    "index": True,
    "similarity": "l2_norm",
    "index_options": {"type": "int8_hnsw", "m": 16, "ef_construction": 100},
}
ARXIV_INDEX_SETTINGS = {
    "analysis": {
        "normalizer": {
            "scholar_title": {"type": "custom", "filter": ["lowercase"]},
        }
    }
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

    def __init__(
        self,
        es: AsyncElasticsearch,
        alias: str,
    ):
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
        fields = [
            ("title", 3),
            ("metadata.s2.tldr.text", 2),
            ("abstract", 1),
        ]
        response = await self.es.search(
            index=self.alias,
            size=limit,
            track_total_hits=False,
            source_excludes=["embedding"],
            query={
                "bool": {
                    # 每个逻辑字段只选原形/词干中的最高分；字段之间也不直接累加。
                    "must": [
                        {
                            "dis_max": {
                                "queries": [
                                    {
                                        "multi_match": {
                                            "query": query,
                                            "fields": [f"{field}.en", field],
                                            "type": "best_fields",
                                            "operator": "or",
                                            "tie_breaker": 0,
                                            "boost": weight,
                                        }
                                    }
                                    for field, weight in fields
                                ],
                                "tie_breaker": 0.1,
                            }
                        }
                    ],
                    "should": [
                        {
                            "term": {
                                "title.exact": {
                                    "value": " ".join(query.split()).lower(),
                                    "boost": 8,
                                }
                            }
                        },
                        {
                            "match_phrase": {
                                "title": {
                                    "query": query,
                                    "boost": 4,
                                }
                            }
                        },
                    ],
                    "filter": self._filters(
                        categories, published_after, published_before
                    ),
                }
            },
        )
        return [self._to_paper(hit["_source"]) for hit in response["hits"]["hits"]]

    async def search_vector(
        self,
        *,
        vector: list[float],
        categories: list[str],
        published_after: date | None,
        published_before: date | None,
        limit: int,
        num_candidates: int = 1000,
    ) -> list[Paper]:
        response = await self.es.search(
            index=self.alias,
            size=limit,
            track_total_hits=False,
            source_excludes=["embedding"],
            knn={
                "field": "embedding",
                "query_vector": vector,
                "k": limit,
                "num_candidates": num_candidates,
                # 必须是 kNN 预过滤；事后过滤会使有资格的候选提前被截断。
                "filter": self._filters(categories, published_after, published_before),
            },
        )
        return [self._to_paper(hit["_source"]) for hit in response["hits"]["hits"]]

    @staticmethod
    def _filters(categories, published_after, published_before):
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
        if published_after is not None:
            bounds["gte"] = published_after.isoformat()
        if published_before is not None:
            bounds["lte"] = published_before.isoformat()
        if bounds:
            filters.append({"range": {"submitted_date": bounds}})
        return filters

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
                    {
                        "size": 2,
                        "_source": {"excludes": ["embedding"]},
                        "query": {"term": {field: identifier.value}},
                    },
                ]
            )
        try:
            response = await self.es.msearch(searches=searches)
        except (ApiError, ConnectionError, ConnectionTimeout) as error:
            # 网络失败在存储接入边界转换；lookup 可据此回退，程序错误继续传播。
            raise ProviderError(provider_error_message(error)) from error
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
