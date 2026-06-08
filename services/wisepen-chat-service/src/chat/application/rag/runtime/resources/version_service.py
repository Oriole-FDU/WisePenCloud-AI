from dataclasses import dataclass

from chat.application.algorithms.hash import stable_hash_json
from chat.application.rag.runtime.models import RagResource
from chat.application.rag.runtime.models import VersionSnapshot


@dataclass(frozen=True, slots=True)
class RagPipelineVersionConfig:
    """RAG pipeline 版本配置。

    - 只包含会影响索引结果的配置版本。
    - 任意字段变化都会产生新的 pipeline_version。
    - 不包含资源内容、资源版本和 reranker 版本。

    Args:
    - chunker_version: 分块器版本。
    - semantic_indexing_text_version: semantic_indexing_text 构造规则版本。
    - keyword_indexing_version: keyword_text 构造规则版本。
    - identifier_extractor_version: identifier_terms 抽取规则版本。
    - dense_embedding_model_version: dense embedding 模型版本。
    - sparse_embedding_model_version: sparse embedding 模型版本。
    - contextual_indexing_version: Context Indexing 策略版本。
    - context_model_version: context 生成模型版本。
    - context_prompt_version: context prompt 版本。
    """

    chunker_version: str
    semantic_indexing_text_version: str
    keyword_indexing_version: str
    identifier_extractor_version: str
    dense_embedding_model_version: str
    sparse_embedding_model_version: str
    contextual_indexing_version: str
    context_model_version: str
    context_prompt_version: str


class RagVersionService:
    """RAG 版本计算服务。

    - resource.version 只用于过期消息判断，不直接进入 index_version。
    - material_hash 表示资源材料变化。
    - pipeline_version 表示索引管线变化。
    - index_version 表示最终索引代次。
    """

    def __init__(self, pipeline_config: RagPipelineVersionConfig) -> None:
        """初始化对象依赖。"""
        self._pipeline_config = pipeline_config
        self.pipeline_version = self._compute_pipeline_version()

    def build_snapshot(self, resource: RagResource) -> VersionSnapshot:
        """构造资源索引版本快照。

        Args:
        - resource: RAG 资源事实对象。

        Returns:
        - 当前资源对应的索引版本快照。
        """

        material_hash = self._compute_material_hash(resource)
        index_version = self._compute_index_version(
            resource=resource,
            material_hash=material_hash,
        )

        return VersionSnapshot(
            resource_version=resource.version,
            material_hash=material_hash,
            pipeline_version=self.pipeline_version,
            index_version=index_version,
        )

    def _compute_material_hash(self, resource: RagResource) -> str:
        """计算资源材料 hash。

        - 包含 resource_kind、resource_id、content_hash、display_name。
        - 不包含 resource.version。
        """

        return stable_hash_json(
            {
                "resource_kind": resource.resource_kind.value,
                "resource_id": resource.resource_id,
                "content_hash": stable_hash_json({"content": resource.content}),
                "display_name": resource.display_name,
            }
        )

    def _compute_pipeline_version(self) -> str:
        """计算 RAG pipeline 版本。"""

        config = self._pipeline_config
        return stable_hash_json(
            {
                "chunker_version": config.chunker_version,
                "semantic_indexing_text_version": config.semantic_indexing_text_version,
                "keyword_indexing_version": config.keyword_indexing_version,
                "identifier_extractor_version": config.identifier_extractor_version,
                "dense_embedding_model_version": config.dense_embedding_model_version,
                "sparse_embedding_model_version": config.sparse_embedding_model_version,
                "contextual_indexing_version": config.contextual_indexing_version,
                "context_model_version": config.context_model_version,
                "context_prompt_version": config.context_prompt_version,
            }
        )

    def _compute_index_version(
        self,
        *,
        resource: RagResource,
        material_hash: str,
    ) -> str:
        """计算最终索引版本。

        - 包含 resource_kind、user_id、resource_id、material_hash、pipeline_version。
        - 不直接包含 resource.version。
        """

        return stable_hash_json(
            {
                "resource_kind": resource.resource_kind.value,
                "user_id": resource.user_id,
                "resource_id": resource.resource_id,
                "material_hash": material_hash,
                "pipeline_version": self.pipeline_version,
            }
        )


