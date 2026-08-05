from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from rag.application.rag.acl.models import RagResourceAclProjection
    from rag.application.rag.graph_projection.models import KnowledgeGraphProjection
    from rag.application.rag.graph_extraction.models import KnowledgeExtractionSource
    from rag.application.rag.ingestion.models import RagContentProjection
    from rag.application.rag.ingestion.revision import (
        RagProjectionCheckpoint,
        RagProjectionStage,
    )


class KnowledgeGraphProjectionSupersededError(RuntimeError):
    """图写入期间正文 revision 已被更新。"""


class RagAclProjectionRepository(Protocol):
    """上游 Resource ACL 在 RAG 侧的本地投影接口。"""

    async def upsert_projection(self, projection: RagResourceAclProjection) -> None:
        """幂等写入或更新单个资源 ACL 投影。"""
        ...

    async def get_projection(
            self, resource_id: str
    ) -> RagResourceAclProjection | None:
        """读取本地已缓存的 ACL 投影；不存在或已失效时返回 None。"""
        ...

    async def get_projections(
            self, resource_ids: Sequence[str]
    ) -> Mapping[str, RagResourceAclProjection]:
        """批量读取多个资源的本地 ACL 投影，仅返回已存在的条目。"""
        ...

    async def load_authoritative_projection(
            self, resource_id: str
    ) -> RagResourceAclProjection | None:
        """从权威源（Java Resource 服务）同步读取最新 ACL 投影。"""
        ...


class RagAclProjectionTarget(Protocol):
    """需要接收 ACL 投影变更通知的下游后端（向量库、图谱库等）。"""

    async def update_acl_projection(
            self, projection: RagResourceAclProjection
    ) -> None:
        """将最新 ACL 投影同步到具体后端的索引结构中。"""
        ...


class RagContentProjectionRepository(Protocol):
    """资源内容投影的两阶段写入接口。"""

    async def stage_projection(
            self, projection: RagContentProjection
    ) -> RagProjectionStage:
        """写入 staging 投影，并返回当前 revision 的处理动作。"""
        ...

    async def apply_projection(self, stage: RagProjectionStage) -> None:
        """通过 checkpoint CAS 将当前 staging revision 提升为 applied。"""
        ...


class RagContentCheckpointRepository(Protocol):
    """资源内容投影的版本检查点读取接口。"""

    async def get_checkpoint(
            self, resource_id: str
    ) -> RagProjectionCheckpoint | None:
        """读取资源的当前投影检查点；不存在时返回 None。"""
        ...

    async def get_applied_revisions(
            self, resource_ids: Sequence[str]
    ) -> Mapping[str, str]:
        """批量读取各资源已应用的 content_revision，仅返回已存在条目。"""
        ...


class RagKnowledgeExtractionSourceRepository(Protocol):
    """图抽取读取当前 applied 正文投影的接口。"""

    async def load_applied_extraction_source(
            self, resource_id: str
    ) -> KnowledgeExtractionSource | None:
        """读取当前 applied revision 的图抽取输入；不存在时返回 None。"""
        ...


class KnowledgeGraphProjectionRepository(Protocol):
    """资源级知识图谱投影的写入与版本控制接口。"""

    async def initialize(self) -> None:
        """初始化约束/索引；幂等，重复调用应安全。"""
        ...

    async def is_projection_applied(
            self,
            *,
            resource_id: str,
            content_revision: str,
    ) -> bool:
        """判断指定 content_revision 的图投影是否已经应用，避免重复抽取。"""
        ...

    async def invalidate_projection(
            self,
            *,
            resource_id: str,
            content_revision: str,
    ) -> None:
        """在写入新图前主动使旧关系失效，防止抽取期间继续暴露过期关系。"""
        ...

    async def apply_projection(
            self,
            *,
            projection: KnowledgeGraphProjection,
    ) -> None:
        """按 revision 提交图投影；遇到并发版本冲突时抛出 KnowledgeGraphProjectionSupersededError。"""
        ...

    async def update_acl_projection(
            self, projection: RagResourceAclProjection
    ) -> None:
        """将最新 ACL 投影同步到图谱库的 Resource 节点。"""
        ...
