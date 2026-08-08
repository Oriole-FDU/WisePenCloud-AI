from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from rag.application.rag.acl.models import RagResourceAclProjection
    from rag.application.rag.graph_projection.models import KnowledgeGraphProjection
    from rag.application.rag.ingestion.models import RagContentProjection
    from rag.application.rag.ingestion.revision import (
        RagProjectionCheckpoint,
        RagProjectionStage,
    )


class KnowledgeGraphProjectionSupersededError(RuntimeError):
    """图写入期间正文 revision 已被更新。"""


class RagAclProjectionRepository(ABC):
    """上游 Resource ACL 在 RAG 侧的本地投影接口。"""

    @abstractmethod
    async def upsert_projection(self, projection: RagResourceAclProjection) -> None:
        """幂等写入或更新单个资源 ACL 投影。"""
        pass

    @abstractmethod
    async def get_projection(
        self,
        resource_id: str,
    ) -> RagResourceAclProjection | None:
        """读取本地已缓存的 ACL 投影；不存在或已失效时返回 None。"""
        pass

    @abstractmethod
    async def get_projections(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, RagResourceAclProjection]:
        """批量读取多个资源的本地 ACL 投影，仅返回已存在的条目。"""
        pass

    @abstractmethod
    async def load_authoritative_projection(
        self,
        resource_id: str,
    ) -> RagResourceAclProjection | None:
        """从权威源同步读取最新 ACL 投影。"""
        pass


class RagAclProjectionTarget(ABC):
    """需要接收 ACL 投影变更通知的下游持久化后端。"""

    @abstractmethod
    async def update_acl_projection(
        self,
        projection: RagResourceAclProjection,
    ) -> None:
        """将最新 ACL 投影同步到具体后端的索引结构中。"""
        pass


class RagContentProjectionRepository(ABC):
    """资源内容投影的两阶段写入接口。"""

    @abstractmethod
    async def stage_projection(
        self,
        projection: RagContentProjection,
    ) -> RagProjectionStage:
        """写入 staging 投影，并返回当前 revision 的处理动作。"""
        pass

    @abstractmethod
    async def apply_projection(self, stage: RagProjectionStage) -> None:
        """通过 checkpoint CAS 将当前 staging revision 提升为 applied。"""
        pass


class RagContentCheckpointRepository(ABC):
    """资源内容投影的版本检查点读取接口。"""

    @abstractmethod
    async def get_checkpoint(
        self,
        resource_id: str,
    ) -> RagProjectionCheckpoint | None:
        """读取资源的当前投影检查点；不存在时返回 None。"""
        pass

    @abstractmethod
    async def get_applied_revisions(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, str]:
        """批量读取各资源已应用的 content_revision。"""
        pass


class KnowledgeGraphProjectionRepository(ABC):
    """资源级知识图谱投影的写入与版本控制接口。"""

    @abstractmethod
    async def initialize(self) -> None:
        """幂等初始化图约束与索引。"""
        pass

    @abstractmethod
    async def is_projection_applied(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> bool:
        """判断指定 content_revision 的图投影是否已经应用。"""
        pass

    @abstractmethod
    async def invalidate_projection(
        self,
        *,
        resource_id: str,
        content_revision: str,
    ) -> None:
        """在写入新图前使旧关系失效。"""
        pass

    @abstractmethod
    async def apply_projection(
        self,
        *,
        projection: KnowledgeGraphProjection,
    ) -> None:
        """按 revision 提交图投影；并发版本冲突时抛出 superseded 异常。"""
        pass

    @abstractmethod
    async def update_acl_projection(
        self,
        projection: RagResourceAclProjection,
    ) -> None:
        """将最新 ACL 投影同步到图谱 Resource 节点。"""
        pass
