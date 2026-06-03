from typing import List, Optional

from chat.application.rag.domain.index_publication import RagIndexManifest
from chat.application.rag.domain.ports import RagManifestRepository
from chat.application.rag.enums import ResourceKind


class RagManifestResolver:
    """RAG 线上 Manifest 解析器。

    - 为检索链路读取当前用户已发布的索引版本。
    """

    def __init__(self, manifest_repository: RagManifestRepository) -> None:
        """初始化对象依赖。"""
        self._manifest_repository = manifest_repository

    async def resolve_user_manifests(
            self,
            user_id: str,
            resource_kinds: Optional[List[ResourceKind]] = None,
    ) -> List[RagIndexManifest]:
        """读取用户当前可检索的 Manifest。

        Args:
        - user_id: 用户 ID。
        - resource_kinds: 可选资源类型过滤。

        Returns:
        - 当前可检索的 Manifest 列表。
        """

        manifests = await self._manifest_repository.list_by_user(user_id)

        if resource_kinds is None:
            return manifests

        return [
            manifest
            for manifest in manifests
            if manifest.resource_kind in set(resource_kinds)
        ]
