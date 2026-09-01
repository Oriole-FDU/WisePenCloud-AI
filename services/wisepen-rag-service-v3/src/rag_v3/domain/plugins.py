"""垂类 metadata 与图谱生产插件的最小注册边界。"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Protocol

from rag_v3.domain.graph import GraphEdge, GraphNode, Ontology
from rag_v3.domain.models import Document, DocumentMetadata, GeneralDocumentMetadata


class DeterministicGraphProducer(Protocol):
    """从已校验 metadata 直接生成图元，不生成文本 Evidence。"""

    def produce(self, document: Document) -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]: ...


class GraphPlugin:
    """一个垂类的 metadata 类型、Ontology 与可选图谱生产能力。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        metadata_type: type[DocumentMetadata],
        ontology: Ontology,
        deterministic_producer: DeterministicGraphProducer | None = None,
        enable_llm_extraction: bool = True,
        metadata_filter_values: Callable[
            [Document], Mapping[str, str | int | float | bool]
        ]
        | None = None,
    ) -> None:
        if not plugin_id.strip():
            raise ValueError("plugin_id must not be empty")
        self.plugin_id = plugin_id
        self.metadata_type = metadata_type
        self.ontology = ontology
        self.deterministic_producer = deterministic_producer
        self.enable_llm_extraction = enable_llm_extraction
        self._metadata_filter_values = metadata_filter_values

    def matches(self, metadata: DocumentMetadata) -> bool:
        return type(metadata) is self.metadata_type

    def filter_values(self, document: Document) -> dict[str, str | int | float | bool]:
        """只由插件决定哪些强类型 metadata 可以成为查询过滤字段。"""
        if self._metadata_filter_values is None:
            return {}
        return dict(self._metadata_filter_values(document))


class DocumentMetadataRegistry:
    """统一编码已注册 metadata，Mongo 不接受无类型的自由字典。"""

    def __init__(self, plugins: Sequence[GraphPlugin] = ()) -> None:
        types = [GeneralDocumentMetadata, *(plugin.metadata_type for plugin in plugins)]
        self._types = {metadata_type.model_fields["document_type"].default: metadata_type for metadata_type in types}
        if len(self._types) != len(types):
            raise ValueError("document metadata types must be unique")

    def encode(self, metadata: DocumentMetadata) -> dict[str, object]:
        if type(metadata) not in self._types.values():
            raise ValueError(f"unregistered document metadata: {type(metadata).__name__}")
        return metadata.model_dump(mode="json")

    def decode(self, value: dict[str, object]) -> DocumentMetadata:
        document_type = value.get("document_type")
        if not isinstance(document_type, str):
            raise TypeError("persisted document metadata has no document_type")
        metadata_type = self._types.get(document_type)
        if metadata_type is None:
            raise ValueError(f"unregistered document metadata type: {document_type}")
        return metadata_type.model_validate(value)
