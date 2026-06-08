from typing import Any, Dict, List

from qdrant_client import models

from chat.application.rag.runtime.retrieval.channels.models import RagIndexScope


class RagScopeFilterBuilder:
    """RAG 检索作用域过滤器构造器。"""

    def __init__(self, scopes: List[RagIndexScope]) -> None:
        self._scopes = scopes

    def to_qdrant_filter(self) -> models.Filter:
        """生成 Qdrant 多资源多版本过滤器。"""
        return models.Filter(
            should=[
                models.Filter(
                    must=[
                        models.FieldCondition(
                            key="user_id",
                            match=models.MatchValue(value=scope.user_id),
                        ),
                        models.FieldCondition(
                            key="resource_kind",
                            match=models.MatchValue(value=scope.resource_kind.value),
                        ),
                        models.FieldCondition(
                            key="resource_id",
                            match=models.MatchValue(value=scope.resource_id),
                        ),
                        models.FieldCondition(
                            key="index_version",
                            match=models.MatchValue(value=scope.index_version),
                        ),
                    ]
                )
                for scope in self._scopes
            ]
        )

    def to_elasticsearch_filter(self) -> Dict[str, Any]:
        """生成 Elasticsearch bool filter。"""
        return {
            "bool": {
                "should": [
                    {
                        "bool": {
                            "filter": [
                                {"term": {"user_id": scope.user_id}},
                                {"term": {"resource_kind": scope.resource_kind.value}},
                                {"term": {"resource_id": scope.resource_id}},
                                {"term": {"index_version": scope.index_version}},
                            ]
                        }
                    }
                    for scope in self._scopes
                ],
                "minimum_should_match": 1,
            }
        }

    def to_mongo_match_filter(self) -> Dict[str, Any]:
        """生成 Mongo 查询片段，用于后续权限范围对齐。"""
        return {
            "$or": [
                {
                    "user_id": scope.user_id,
                    "resource_kind": scope.resource_kind.value,
                    "resource_id": scope.resource_id,
                    "index_version": scope.index_version,
                }
                for scope in self._scopes
            ]
        }
