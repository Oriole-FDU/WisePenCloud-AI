from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256

from .models import RagContentProjection

_CONTENT_PROJECTION_SCHEMA_VERSION = "rag_content_projection:v6"


class RagProjectionStageAction(StrEnum):
    """Projection 处理状态。"""

    STAGED = "staged"
    ALREADY_APPLIED = "already_applied"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class RagProjectionCheckpoint:
    """记录 Projection 消费过程中的版本状态。

    staged:
        已完成预处理，但尚未确认最终应用。

    applied:
        已成功写入下游索引。
    """

    resource_id: str

    staged_content_revision: str | None = None
    staged_document_version: int | None = None

    applied_content_revision: str | None = None
    applied_document_version: int | None = None


@dataclass(frozen=True, slots=True)
class RagProjectionStage:
    """Projection 当前消费任务的幂等判断结果。"""

    resource_id: str
    document_version: int
    content_revision: str
    action: RagProjectionStageAction


def prepare_projection_stage(
        projection: RagContentProjection,
        checkpoint: RagProjectionCheckpoint | None,
) -> RagProjectionStage:
    """判断当前 Projection 是否需要继续处理。

    根据 checkpoint 判断：
    - 已经成功应用：跳过；
    - 当前版本落后于已有状态：丢弃；
    - 其他情况：进入 staged 流程。
    """
    content_revision = _content_revision_id(projection)
    action = RagProjectionStageAction.STAGED

    if checkpoint is not None:
        if checkpoint.applied_content_revision == content_revision:
            # 完全相同版本已经写入，无需重复处理。
            action = RagProjectionStageAction.ALREADY_APPLIED
        elif (
                checkpoint.applied_document_version is not None
                and checkpoint.applied_document_version > projection.document_version
        ):
            # 已应用更新版本，当前消息属于旧事件。
            action = RagProjectionStageAction.STALE
        # staged 不是完成态；相同 revision 重试和同版本内容修正都继续处理。
        elif (
                checkpoint.staged_document_version is not None
                and checkpoint.staged_document_version > projection.document_version
        ):
            # 存在更新的 staged 版本，当前事件无需继续。
            action = RagProjectionStageAction.STALE

    return RagProjectionStage(
        resource_id=projection.resource_id,
        document_version=projection.document_version,
        content_revision=content_revision,
        action=action,
    )


def _content_revision_id(projection: RagContentProjection) -> str:
    """生成 Projection 内容版本指纹。

    revision 不等同于 document_version：

    document_version:
        上游文档版本。

    content_revision:
        当前 RAG 投影结果版本，额外绑定：
        - projection schema；
        - 内容 hash。

    当投影结构变化但原文未变时，可通过 schema version 强制重新生成。
    """
    value = "\0".join(
        (
            projection.resource_id,
            str(projection.document_version),
            projection.content_hash,
            _CONTENT_PROJECTION_SCHEMA_VERSION,
        )
    )
    digest = sha256(value.encode("utf-8")).hexdigest()
    return f"rrev_{digest[:32]}"
