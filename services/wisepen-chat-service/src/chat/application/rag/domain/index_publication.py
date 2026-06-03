from dataclasses import dataclass

from ..enums import ResourceKind


@dataclass(frozen=True, slots=True)
class VersionSnapshot:
    """版本快照。

    记录资源在某一时刻的版本信息，
    用于判断索引是否需要重新构建。
    """

    resource_version: int
    material_hash: str
    pipeline_version: str
    index_version: str


@dataclass(frozen=True, slots=True)
class RagIndexManifest:
    """RAG 索引发布清单。

    记录某个资源已发布的索引元数据，
    包括版本、内容哈希以及流水线版本信息。
    """

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    resource_version: int
    material_hash: str
    pipeline_version: str
    current_index_version: str


@dataclass(frozen=True, slots=True)
class RagIndexMessage:
    """RAG 索引队列消息。

    当资源内容变更时，将索引任务推入消息队列，
    由消费者异步执行索引构建和发布。
    """

    user_id: str
    resource_kind: ResourceKind
    resource_id: str
    expected_version: int
    pipeline_version: str
    target_index_version: str
    priority: int = 100


def build_index_message(
    *,
    resource,
    version_snapshot: VersionSnapshot,
    priority: int = 100,
) -> RagIndexMessage:
    """从资源和版本快照构建索引队列消息。

    Args:
        resource: 需要索引的资源对象（必须有 user_id, resource_kind, resource_id 属性）。
        version_snapshot: 当前版本快照。
        priority: 消息优先级（数值越小优先级越高）。

    Returns:
        可用于推送至索引队列的 RagIndexMessage。
    """
    return RagIndexMessage(
        user_id=resource.user_id,
        resource_kind=resource.resource_kind,
        resource_id=resource.resource_id,
        expected_version=version_snapshot.resource_version,
        pipeline_version=version_snapshot.pipeline_version,
        target_index_version=version_snapshot.index_version,
        priority=priority,
    )
