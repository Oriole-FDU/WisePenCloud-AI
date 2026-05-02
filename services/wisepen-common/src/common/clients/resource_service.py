from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

from common.core.constants import SecurityConstants
from common.core.exceptions import RpcError
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-resource-service"


@dataclass
class TagTreeNode:
    tag_id: str
    tag_name: str
    parent_id: str = "0"
    children: List["TagTreeNode"] = field(default_factory=list)


class ResourceServiceClient:
    """wisepen-resource-service typed facade"""

    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name

    async def get_personal_tag_tree(self, user_id: str) -> List[TagTreeNode]:
        data = await self._rpc.get(
            self._service_name,
            "/resource/tag/getTagTree",
            headers={SecurityConstants.HEADER_USER_ID: str(user_id)},
        )
        if not isinstance(data, list):
            raise RpcError(
                service_name=self._service_name,
                path="/resource/tag/getTagTree",
                msg=f"unexpected data payload: {data!r}",
            )
        return [self._build_tag_tree_node(item) for item in data if isinstance(item, dict)]

    async def create_personal_tag(
        self,
        user_id: str,
        tag_name: str,
        parent_id: Optional[str] = None,
    ) -> str:
        data = await self._rpc.post(
            self._service_name,
            "/resource/tag/addTag",
            json={
                "parentId": parent_id or "0",
                "tagName": tag_name,
            },
            headers={SecurityConstants.HEADER_USER_ID: str(user_id)},
        )
        if not data:
            raise RpcError(
                service_name=self._service_name,
                path="/resource/tag/addTag",
                msg=f"unexpected data payload: {data!r}",
            )
        return str(data)

    async def update_resource_tags(
        self,
        user_id: str,
        resource_id: str,
        tag_ids: List[str],
    ) -> None:
        await self._rpc.post(
            self._service_name,
            "/resource/item/updateTags",
            json={
                "resourceId": resource_id,
                "tagIds": tag_ids,
            },
            headers={SecurityConstants.HEADER_USER_ID: str(user_id)},
        )

    def _build_tag_tree_node(self, data: dict) -> TagTreeNode:
        return TagTreeNode(
            tag_id=str(data.get("tagId") or ""),
            tag_name=str(data.get("tagName") or ""),
            parent_id=str(data.get("parentId") or "0"),
            children=[
                self._build_tag_tree_node(child)
                for child in data.get("children") or []
                if isinstance(child, dict)
            ],
        )
