"""模型生成派生产物的持久化 port。"""

from collections.abc import Mapping, Sequence
from typing import Literal, Protocol


class GenerationArtifactStore(Protocol):
    """按资源和派生产物类别读写模型生成结果。"""

    async def get_many(
        self,
        *,
        resource_id: str,
        artifact_kind: Literal["context", "graph"],
        artifact_keys: Sequence[str],
    ) -> Mapping[str, str]:
        """返回已命中的 key/value；未命中 key 不出现在结果中。"""

    async def set_many(
        self,
        *,
        resource_id: str,
        artifact_kind: Literal["context", "graph"],
        artifacts: Mapping[str, str],
    ) -> None:
        """按资源和类别幂等覆盖派生产物。"""

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        """删除指定资源的全部生成派生产物。"""
