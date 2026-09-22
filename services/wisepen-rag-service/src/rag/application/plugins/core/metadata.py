"""垂类 metadata 的模型与持久化编解码契约。"""

from pydantic import BaseModel, ConfigDict, Field


class DocumentMetadata(BaseModel):
    """Document 持久化的强类型 metadata 基类；具体类型由插件注册。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    document_type: str = Field(
        description="文档的稳定类型标识，由上游确定性提供，用于插件路由和多态恢复。"
    )


class GeneralDocumentMetadata(DocumentMetadata):
    """没有匹配垂类插件的通用文档 metadata。"""

    document_type: str = Field(default="general", description="通用文档类型标识。")


class DocChunkMetadata(BaseModel):
    """由文档和切块确定性派生的持久化 Chunk metadata 基类。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    chunk_type: str = Field(
        description="切块 metadata 的稳定类型标识，由准备阶段确定性派生。"
    )


class GeneralChunkMetadata(DocChunkMetadata):
    """没有匹配垂类规则的通用 Chunk metadata。"""

    chunk_type: str = Field(default="general", description="通用切块类型标识。")


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
