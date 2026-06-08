import hashlib
import re
from dataclasses import dataclass
from typing import Any, Dict, List

from elasticsearch import AsyncElasticsearch
from elasticsearch.helpers import async_bulk

from chat.application.rag.enums import ResourceKind
from chat.application.rag.runtime.models import IndexingTextPair
from chat.application.rag.runtime.models import SearchChunk

_KEYWORD_INDEX_SETTINGS = {
    "number_of_shards": 1,
    "number_of_replicas": 0,
}

_KEYWORD_INDEX_MAPPINGS = {
    "dynamic": "strict",
    "properties": {
        "user_id": {"type": "keyword"},
        "resource_kind": {"type": "keyword"},
        "resource_id": {"type": "keyword"},
        "index_version": {"type": "keyword"},
        "chunk_id": {"type": "keyword"},
        "parent_chunk_id": {"type": "keyword"},
        "parent_chunk_index": {"type": "integer"},
        "chunk_index": {"type": "integer"},
        "display_name": {
            "type": "text",
            "fields": {
                "keyword": {"type": "keyword"},
            },
        },
        "heading_path": {
            "type": "text",
            "fields": {
                "keyword": {"type": "keyword"},
            },
        },
        "keyword_text": {"type": "text"},
    },
}


class ElasticsearchKeywordIndexError(RuntimeError):
    """Elasticsearch keyword index 写入失败。"""


class ElasticsearchKeywordIndexer:
    """Elasticsearch keyword exact indexer。

    - 负责写入 SearchChunk 的 keyword_text。
    - display_name 用于标题 / 文档名 boost。
    - heading_path 预留给后续章节路径 boost。
    - payload 字段用于检索时强过滤。
    - 不保存父块原文。
    - 不索引 LLM context_text。
    - 不负责 dense vector。
    - 不负责 Manifest 发布。
    """

    def __init__(
        self,
        client: AsyncElasticsearch,
        index_name: str,
    ) -> None:
        """初始化对象依赖。"""
        self._client = client
        self._index_name = index_name

    async def ensure_index(self) -> None:
        """确保 keyword index 存在。"""

        if await self._client.indices.exists(index=self._index_name):
            return

        await self._client.indices.create(
            index=self._index_name,
            settings=_KEYWORD_INDEX_SETTINGS,
            mappings=_KEYWORD_INDEX_MAPPINGS,
        )

    async def upsert_keyword_chunks(
        self,
        user_id: str,
        index_version: str,
        display_name: str,
        heading_path: str,
        search_chunks: List[SearchChunk],
        indexing_text_pairs: Dict[str, IndexingTextPair],
    ) -> None:
        """批量写入 keyword docs。"""

        if display_name != display_name.strip():
            raise ElasticsearchKeywordIndexError(
                "display_name must not contain leading or trailing whitespace."
            )

        if heading_path != heading_path.strip():
            raise ElasticsearchKeywordIndexError(
                "heading_path must not contain leading or trailing whitespace."
            )

        if not search_chunks:
            return

        actions: List[Dict[str, Any]] = []

        for chunk in search_chunks:
            indexing_text_pair = indexing_text_pairs.get(chunk.chunk_id)
            if indexing_text_pair is None:
                raise ElasticsearchKeywordIndexError(
                    f"Indexing text not found for search chunk: {chunk.chunk_id}"
                )

            actions.append(
                {
                    "_op_type": "index",
                    "_index": self._index_name,
                    "_id": hashlib.sha256(
                        (
                            f"{user_id}:"
                            f"{chunk.resource_kind.value}:"
                            f"{chunk.resource_id}:"
                            f"{index_version}:"
                            f"{chunk.chunk_id}"
                        ).encode("utf-8").hexdigest()
                    ),
                    "_source": {
                        "user_id": user_id,
                        "resource_kind": chunk.resource_kind.value,
                        "resource_id": chunk.resource_id,
                        "index_version": index_version,
                        "chunk_id": chunk.chunk_id,
                        "parent_chunk_id": chunk.parent_chunk_id,
                        "parent_chunk_index": chunk.parent_chunk_index,
                        "chunk_index": chunk.chunk_index,
                        "display_name": display_name,
                        "heading_path": _extract_heading_path(
                            chunk.text,
                            default=heading_path,
                        ),
                        "keyword_text": indexing_text_pair.keyword_text,
                    },
                }
            )

        _, errors = await async_bulk(
            client=self._client,
            actions=actions,
            refresh=False,
            raise_on_error=False,
            stats_only=False
        )

        if errors:
            failed_summaries = []
            for err in errors:  # type: ignore
                op_type = next(iter(err))
                detail = err[op_type]
                failed_summaries.append(
                    f"id={detail.get('_id', '?')} "
                    f"status={detail.get('status', '?')} "
                    f"reason={detail.get('error', {}).get('reason', '?')}"
                )
            raise ElasticsearchKeywordIndexError(
                f"Elasticsearch keyword bulk index failed "
                f"({len(errors)} docs): {'; '.join(failed_summaries)}"  # type: ignore
            )

    async def delete_by_index_version(
        self,
        user_id: str,
        resource_kind: ResourceKind,
        resource_id: str,
        index_version: str,
    ) -> None:
        """删除指定资源索引版本的 keyword docs。"""

        await self._client.delete_by_query(
            index=self._index_name,
            query={
                "bool": {
                    "filter": [
                        {"term": {"user_id": user_id}},
                        {"term": {"resource_kind": resource_kind.value}},
                        {"term": {"resource_id": resource_id}},
                        {"term": {"index_version": index_version}},
                    ]
                }
            },
            conflicts="proceed",
            refresh=False,
        )


@dataclass(frozen=True, slots=True)
class ElasticsearchClientConfig:
    """Elasticsearch client 配置。"""

    uris: str
    username: str
    password: str


def build_elasticsearch_client(config: ElasticsearchClientConfig) -> AsyncElasticsearch:
    """构造 Elasticsearch 官方异步客户端。"""

    return AsyncElasticsearch(
        hosts=config.uris,
        basic_auth=(config.username, config.password),
    )


def _extract_heading_path(text: str, default: str) -> str:
    """从注入后的 chunk 文本中提取章节路径。"""
    for line in text.splitlines():
        match = re.match(r"^Section:\s*(.+?)\s*$", line)
        if match is not None:
            return match.group(1).strip()

    return default
