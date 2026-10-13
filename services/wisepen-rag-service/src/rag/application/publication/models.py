"""资源发布用例的流程状态、执行结果与失败语义，不作为独立领域能力。"""

from dataclasses import dataclass
from enum import StrEnum

from rag.core.schema import ContentRevision


@dataclass(frozen=True, slots=True)
class ResourcePublicationState:
    """一个资源的发布事实；destroyed 是永久逻辑销毁，不表示 GC 已完成。"""

    resource_id: str
    staged_revision: ContentRevision | None = None
    applied_revision: ContentRevision | None = None
    destroyed: bool = False  # 永久保留，不能由后续内容提交清除。


class PublicationAction(StrEnum):
    """STAGED 供内部继续构建，其余结果可由 publish 返回。"""

    STAGED = "staged"
    APPLIED = "applied"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"
    DESTROYED = "destroyed"


class ContentIdentityError(ValueError):
    """提交的 Document 身份与其完整正文和版本不符。"""


class ContentConflictError(ValueError):
    """同一资源的同一最高版本出现不同内容摘要。"""


class ContentIncompleteError(ValueError):
    """待发布正文身份或 Chunk 集合与本次构建不一致。"""


class RevisionNotStagedError(RuntimeError):
    """请求发布的 revision 从未暂存，或已不再是可发布候选。"""


class PublicationStateChangedError(RuntimeError):
    """条件更新未完成，重新观察后仍无法给出终态；由调用方决定重试。"""
