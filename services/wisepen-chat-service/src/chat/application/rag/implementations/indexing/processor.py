from abc import ABC, abstractmethod

from chat.application.rag.domain.index_publication import RagIndexMessage
from chat.application.rag.domain.ports import RagManifestRepository
from chat.application.rag.implementations.indexing.index_builder import RagIndexBuilder
from chat.application.rag.implementations.resources.resource_service import ResourceService
from chat.application.rag.implementations.resources.version_service import RagVersionService


class RagIndexMessageProcessor(ABC):
    """RAG 索引消息处理器接口。

    - Worker 只负责队列消费和 ACK。
    - Processor 负责具体索引逻辑。
    - process 正常返回表示消息处理成功。
    - process 抛出异常表示消息处理失败，Worker 不会 ACK 当前失败消息。
    """

    @abstractmethod
    async def process(self, message: RagIndexMessage) -> None:
        """处理索引消息。

        过滤流程：
        1. 检查资源是否存在且未被删除
        2. 检查消息版本与资源事实版本是否一致
        3. 检查 Manifest 是否已被发布（避免重复构建）
        全部通过后委托 IndexBuilder 执行实际索引。
        """
        pass


class RagIndexProcessor(RagIndexMessageProcessor):
    """RAG 索引消息处理器。

    - 负责过滤已经失效的索引消息。
    - 资源不存在、已删除或版本不匹配时直接跳过。
    - 跳过失效消息属于正常处理结果，Worker 可以 ACK。
    - 只有当前资源状态与消息版本完全一致时，才进入 IndexBuilder。
    """

    def __init__(
        self,
        resource_service: ResourceService,
        version_service: RagVersionService,
        index_builder: RagIndexBuilder,
        manifest_repository: RagManifestRepository,
    ) -> None:
        """初始化索引消息处理器。

        Args:
            resource_service: 资源服务，用于获取资源当前状态。
            version_service: 版本快照构建服务。
            index_builder: 索引构建器。
            manifest_repository: 索引清单仓储，用于去重检查。
        """
        self._resource_service = resource_service
        self._version_service = version_service
        self._index_builder = index_builder
        self._manifest_repository = manifest_repository

    async def process(self, message: RagIndexMessage) -> None:
        """处理索引消息。

        - 基于当前 Resource Store 状态判断消息是否仍然有效。
        - 失效消息直接 return，不抛异常。
        - IndexBuilder 抛出的异常才表示真实索引失败。
        """

        resource = await self._resource_service.get(
            resource_kind=message.resource_kind,
            user_id=message.user_id,
            resource_id=message.resource_id,
        )

        # 资源已不存在或已被逻辑删除，说明该索引消息已经失效。
        if resource is None or resource.is_deleted:
            return

        snapshot = self._version_service.build_snapshot(resource)

        # 消息版本必须与当前资源事实版本和索引版本完全一致。
        # 否则说明该消息已经被更新后的资源状态淘汰。
        if (
            resource.version != message.expected_version
            or snapshot.pipeline_version != message.pipeline_version
            or snapshot.index_version != message.target_index_version
        ):
            return

        manifest = await self._manifest_repository.get_by_resource(
            user_id=message.user_id,
            resource_id=message.resource_id,
            resource_kind=message.resource_kind,
        )

        # 如果 manifest 已经存在，则跳过
        if (
            manifest is not None
            and manifest.current_index_version == message.target_index_version
        ):
            return

        await self._index_builder.build(
            resource=resource,
            version_snapshot=snapshot,
        )
