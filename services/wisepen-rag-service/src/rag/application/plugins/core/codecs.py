"""插件 metadata 的持久化多态编解码。"""

from rag.application.plugins.core.models import (
    DocChunkMetadata,
    DocumentMetadata,
    GeneralChunkMetadata,
    GeneralDocumentMetadata,
)


class DocumentMetadataCodec:
    """按 document_type 编解码已注册的文档 metadata。"""

    def __init__(self, metadata_types: list[type[DocumentMetadata]] | None = None) -> None:
        types = [GeneralDocumentMetadata, *(metadata_types or [])]
        self._types = {
            metadata_type.model_fields["document_type"].default: metadata_type
            for metadata_type in types
        }
        if len(self._types) != len(types):
            raise ValueError("document metadata types must be unique")

    def encode(self, metadata: DocumentMetadata) -> dict[str, object]:
        metadata_type = self._types.get(metadata.document_type)
        if metadata_type is not type(metadata):
            raise ValueError(f"unregistered document metadata: {type(metadata).__name__}")
        return metadata.model_dump(mode="json")

    def decode(self, value: dict[str, object]) -> DocumentMetadata:
        document_type = value.get("document_type")
        if not isinstance(document_type, str) or document_type not in self._types:
            raise ValueError("unregistered document metadata type")
        return self._types[document_type].model_validate(value)


class DocChunkMetadataCodec:
    """按 chunk_type 编解码已注册的 Chunk metadata。"""

    def __init__(self, metadata_types: list[type[DocChunkMetadata]] | None = None) -> None:
        types = [GeneralChunkMetadata, *(metadata_types or [])]
        self._types = {
            metadata_type.model_fields["chunk_type"].default: metadata_type
            for metadata_type in types
        }
        if len(self._types) != len(types):
            raise ValueError("chunk metadata types must be unique")

    def encode(self, metadata: DocChunkMetadata) -> dict[str, object]:
        metadata_type = self._types.get(metadata.chunk_type)
        if metadata_type is not type(metadata):
            raise ValueError(f"unregistered chunk metadata: {type(metadata).__name__}")
        return metadata.model_dump(mode="json")

    def decode(self, value: dict[str, object]) -> DocChunkMetadata:
        chunk_type = value.get("chunk_type")
        if not isinstance(chunk_type, str) or chunk_type not in self._types:
            raise ValueError("unregistered chunk metadata type")
        return self._types[chunk_type].model_validate(value)
