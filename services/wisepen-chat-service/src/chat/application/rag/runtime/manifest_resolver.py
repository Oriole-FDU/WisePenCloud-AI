from typing import Dict, List, Optional

from chat.application.rag.enums import ResourceKind
from chat.application.rag.permissions import RagGroupRole
from chat.application.rag.runtime.models import RagIndexManifest
from chat.application.rag.runtime.persistence.interfaces import RagManifestRepository


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
            group_role_map: Dict[str, RagGroupRole],
            resource_kinds: Optional[List[ResourceKind]] = None,
    ) -> List[RagIndexManifest]:
        """读取用户当前可检索的 Manifest。

        Args:
        - user_id: 用户 ID。
        - resource_kinds: 可选资源类型过滤。

        Returns:
        - 当前可检索的 Manifest 列表。
        """

        return await self._manifest_repository.list_visible_manifests(
            user_id=user_id,
            group_role_map=group_role_map,
            resource_kinds=resource_kinds,
        )
