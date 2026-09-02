"""插件与垂类共享的强类型 metadata 模型。"""

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
