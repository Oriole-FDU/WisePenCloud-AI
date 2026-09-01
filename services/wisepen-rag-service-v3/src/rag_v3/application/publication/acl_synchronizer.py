"""将上游 ACL 刷新为 RAG 在线判权副本。"""

from collections.abc import Sequence

from rag_v3.domain.repositories.acl import (
    AuthoritativeAclReader,
    ResourceAclRepository,
)


class AclSynchronizer:
    """批量读取上游 ACL，并用单调 revision 写入 RAG 本地副本。"""

    def __init__(
        self,
        *,
        authoritative_reader: AuthoritativeAclReader,
        local_repository: ResourceAclRepository,
    ) -> None:
        self._authoritative_reader = authoritative_reader
        self._local_repository = local_repository

    async def synchronize(self, resource_ids: Sequence[str]) -> list[str]:
        """返回成功写入或同 revision 幂等确认的资源 ID。"""
        unique_resource_ids = list(dict.fromkeys(resource_ids))
        source_acls = await self._authoritative_reader.get_resource_acls(
            unique_resource_ids
        )
        synchronized: list[str] = []
        for resource_id in unique_resource_ids:
            resource_acl = source_acls.get(resource_id)
            if resource_acl is None:
                continue
            if await self._local_repository.save_if_newer(resource_acl):
                synchronized.append(resource_id)
        return synchronized
