from typing import Any, Dict, List

from elasticsearch import AsyncElasticsearch

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.retrieval.channels.models import (
    ChannelRetrievalResult,
    RagIndexScope,
    RagRetrievedCandidate,
)
from chat.application.rag.runtime.retrieval.enums import RetrievalChannel
from chat.application.rag.runtime.retrieval.retrievers.scope_filter import RagScopeFilterBuilder


class ElasticsearchRetrievalError(RuntimeError):
    """Elasticsearch 检索链路异常。"""


class ElasticsearchKeywordRetriever:
    """Elasticsearch 关键词精准召回器。

    - 死守物理原文，通过 `keyword_text` 字段提供确定性的文本硬匹配召回通道（Keyword Exact Match）。
    - 所有 DSL 查询必须强绑定多维沙箱作用域（RagIndexScope），以在数据访问层隔离多租户及影子版本。
    """

    def __init__(
            self,
            client: AsyncElasticsearch,
            index_name: str,
    ) -> None:
        """初始化对象依赖。"""
        self._client = client
        self._index_name = index_name

    async def retrieve_keyword(
            self,
            *,
            query: str,
            scopes: List[RagIndexScope],
            top_k: int,
    ) -> ChannelRetrievalResult:
        """执行第三路：Elasticsearch 关键词精准文本召回。"""

        if not scopes:
            return ChannelRetrievalResult(
                channel=RetrievalChannel.KEYWORD_EXACT,
                candidates=[],
            )

        # 多字段 field boost 动态方法组装
        response = await self._client.search(
            index=self._index_name,
            query=self._build_keyword_query(
                query=query,
                scope_filter=RagScopeFilterBuilder(scopes).to_elasticsearch_filter(),
            ),
            size=top_k,
        )

        if "hits" not in response or "hits" not in response["hits"]:
            raise ElasticsearchRetrievalError("Malformed Elasticsearch response structure.")

        hits = response["hits"]["hits"]

        return ChannelRetrievalResult(
            channel=RetrievalChannel.KEYWORD_EXACT,
            candidates=[
                self._to_candidate(hit=hit, matched_query=query)
                for hit in hits
            ],
        )

    # 在指定位置插入多字段 field boost DSL 构造器
    def _build_keyword_query(
        self,
        *,
        query: str,
        scope_filter: Dict[str, Any],
    ) -> Dict[str, Any]:
        """构建 keyword_exact 查询 DSL。

        - keyword_text: 原文 keyword 检索主字段。
        - display_name: note title / document_name boost。
        - heading_path: 后续章节路径 boost，当前可以为空。
        - 不使用 identifier_terms。
        """
        return {
            "bool": {
                "should": [
                    {
                        "match_phrase": {
                            "keyword_text": {
                                "query": query,
                                "boost": 1.5,
                            }
                        }
                    },
                    {
                        "match": {
                            "keyword_text": {
                                "query": query,
                                "boost": 1.0,
                            }
                        }
                    },
                    {
                        "match": {
                            "display_name": {
                                "query": query,
                                "boost": 2.0,
                            }
                        }
                    },
                    {
                        "match": {
                            "heading_path": {
                                "query": query,
                                "boost": 1.5,
                            }
                        }
                    },
                ],
                "minimum_should_match": 1,
                "filter": [
                    scope_filter,
                ],
            }
        }

    def _to_candidate(
            self,
            hit: Dict[str, Any],
            matched_query: str,
    ) -> RagRetrievedCandidate:
        """将数据访问层的原始 ES Hit 文档下单向解构映射为业务领域的候选对象模型。"""

        if "_source" not in hit or hit["_source"] is None:
            raise ElasticsearchRetrievalError("Elasticsearch hit _source is missing.")

        source = hit["_source"]

        return RagRetrievedCandidate(
            channel=RetrievalChannel.KEYWORD_EXACT,
            score=float(hit["_score"]),
            user_id=source["user_id"],
            resource_kind=ResourceKind(source["resource_kind"]),
            resource_id=source["resource_id"],
            index_version=source["index_version"],
            chunk_id=source["chunk_id"],
            parent_chunk_id=source["parent_chunk_id"],
            parent_chunk_index=source["parent_chunk_index"],
            chunk_index=source["chunk_index"],
            matched_query=matched_query,
        )
