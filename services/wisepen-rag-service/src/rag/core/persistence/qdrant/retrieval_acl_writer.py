"""将统一 ResourceAcl 事实同步到 Qdrant retrieval payload。"""

from qdrant_client import AsyncQdrantClient
from qdrant_client import models as qdrant_models

from rag.domain.models.acl import ResourceAcl
from rag.domain.repositories.qdrant.retrieval_acl_writer import RetrievalAclWriter


class QdrantRetrievalAclWriter(RetrievalAclWriter):
    """只更新已有资源 points 的 ACL 字段，不解释权限规则。"""

    def __init__(self, *, client: AsyncQdrantClient, collection_name: str) -> None:
        self._client = client
        self._collection_name = collection_name

    async def synchronize(self, resource_acl: ResourceAcl) -> None:
        if not await self._client.collection_exists(self._collection_name):
            return
        await self._client.set_payload(
            collection_name=self._collection_name,
            payload={
                "acl_revision": resource_acl.acl_revision,
                "owner_id": resource_acl.owner_id,
                "readable_users": list(resource_acl.readable_users),
                "excluded_read_users": list(resource_acl.excluded_read_users),
                "group_acls": [
                    {
                        "group_id": group_acl.group_id,
                        "is_readable": group_acl.default_readable,
                        "readable_users": list(group_acl.readable_users),
                        "excluded_read_users": list(
                            group_acl.excluded_read_users
                        ),
                    }
                    for group_acl in resource_acl.group_acls
                ],
            },
            points=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="resource_id",
                        match=qdrant_models.MatchValue(
                            value=resource_acl.resource_id
                        ),
                    ),
                    qdrant_models.FieldCondition(
                        key="acl_revision",
                        range=qdrant_models.Range(lte=resource_acl.acl_revision),
                    ),
                ]
            ),
            wait=True,
        )
