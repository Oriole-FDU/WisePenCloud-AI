"""垂类插件必须实现的 SPI 协议与 RagPlugin 聚合根。"""

from collections.abc import Callable, Mapping
from typing import Protocol

from pydantic import BaseModel

from rag.application.document.models import DocChunk, Document
from rag.application.graph.models import GraphEdge, GraphNode
from rag.application.plugins.core.filters import DeclarativeMetadataFilter
from rag.application.plugins.core.metadata import DocChunkMetadata, DocumentMetadata
from rag.application.plugins.core.ontology import Ontology
from rag.domain.repositories.metadata_filters import MetadataFilterCondition


class ChunkMetadataBuilder(Protocol):
    """为一种文档 metadata 生成对应的持久化 Chunk metadata。"""

    doc_metadata_type: type[DocumentMetadata]
    chunk_metadata_type: type[DocChunkMetadata]

    def build_metadata(
        self,
        *,
        document: Document,
        chunk: DocChunk,
    ) -> DocChunkMetadata: ...


class DeterministicGraphProducer(Protocol):
    """从已校验 metadata 直接生成图元，不生成文本 Evidence。"""

    def produce(self, document: Document) -> tuple[tuple[GraphNode, ...], tuple[GraphEdge, ...]]: ...


class RagPlugin:
    """一种垂类的 metadata、检索过滤与可选图谱生产能力。"""

    def __init__(
        self,
        *,
        plugin_id: str,
        metadata_type: type[DocumentMetadata],
        ontology: Ontology,
        deterministic_producer: DeterministicGraphProducer | None = None,
        enable_llm_extraction: bool = True,
        chunk_selector: Callable[[DocChunk], bool] | None = None,
        chunk_metadata_builder: ChunkMetadataBuilder | None = None,
        metadata_filter_values: Callable[[Document], Mapping[str, str | int | float | bool]] | None = None,
        metadata_filter_type: type[DeclarativeMetadataFilter] | None = None,
    ) -> None:
        if not plugin_id.strip():
            raise ValueError("plugin_id must not be empty")
        if (
            chunk_metadata_builder is not None
            and chunk_metadata_builder.doc_metadata_type is not metadata_type
        ):
            raise ValueError("chunk metadata builder must match plugin metadata type")
        self.plugin_id = plugin_id
        self.metadata_type = metadata_type
        self.ontology = ontology
        self.deterministic_producer = deterministic_producer
        self.enable_llm_extraction = enable_llm_extraction
        self._chunk_selector = chunk_selector
        self.chunk_metadata_builder = chunk_metadata_builder
        self._metadata_filter_values = metadata_filter_values
        self.metadata_filter_type = metadata_filter_type

    def matches(self, metadata: DocumentMetadata) -> bool:
        return type(metadata) is self.metadata_type

    def select_chunks(self, chunks: list[DocChunk]) -> list[DocChunk]:
        """选择 LLM 抽取目标；不改变确定性 producer 的完整 Document 输入。"""
        if self._chunk_selector is None:
            return list(chunks)
        return [chunk for chunk in chunks if self._chunk_selector(chunk)]

    def filter_values(self, document: Document) -> dict[str, str | int | float | bool]:
        if self._metadata_filter_values is None:
            return {}
        return dict(self._metadata_filter_values(document))

    def compile_filter(
        self,
        value: BaseModel | None,
    ) -> tuple[MetadataFilterCondition, ...]:
        if value is None:
            return ()
        if (
            self.metadata_filter_type is None
            or not isinstance(value, self.metadata_filter_type)
        ):
            raise ValueError("graph metadata filter does not match plugin")
        return value.to_conditions()
