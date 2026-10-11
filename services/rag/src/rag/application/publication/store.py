"""发布状态端口；拥有版本与条件更新，不保存正文或检索投影。"""

from typing import Protocol

from rag.application.publication.models import (
    PublicationAction,
    ResourcePublicationState,
)
from rag.core.schema import ContentRevision


class PublicationStore(Protocol):
    """批量读取去重保序、缺失省略；空批量无 I/O，异常直接传播。"""

    async def get_states(
        self, resource_ids: list[str]
    ) -> list[ResourcePublicationState]: ...

    async def stage_revision(self, revision: ContentRevision) -> PublicationAction:
        """按销毁、旧版本、同版本冲突、已发布重放顺序判断，再条件暂存。"""
        ...

    async def apply_revision(self, revision: ContentRevision) -> PublicationAction:
        """仅发布仍为 staged 的未销毁候选；调用方已确认事实与必需投影完成。"""
        ...

    async def destroy_resources(self, resource_ids: list[str]) -> None:
        """永久禁止发布并清空指针；部分成功保留，不清理物理内容。"""
        ...
