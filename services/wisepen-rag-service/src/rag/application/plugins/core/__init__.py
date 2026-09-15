"""垂类插件 SPI Facade；垂类实现只依赖此模块。"""

from importlib import import_module
from typing import Any

__all__ = [
    "ChunkMetadataBuilder",
    "DeclarativeMetadataFilter",
    "DeterministicGraphProducer",
    "DocChunkMetadata",
    "DocChunkMetadataCodec",
    "DocumentMetadataCodec",
    "DocumentMetadata",
    "EntitySpec",
    "Eq",
    "FilterOp",
    "RagPlugin",
    "RagPluginRegistry",
    "Gte",
    "Lte",
    "Ontology",
    "RelationSpec",
]

_EXPORT_MODULES = {
    "ChunkMetadataBuilder": ".plugin",
    "DeterministicGraphProducer": ".plugin",
    "DeclarativeMetadataFilter": ".filters",
    "DocChunkMetadata": ".metadata",
    "DocChunkMetadataCodec": ".metadata",
    "DocumentMetadata": ".metadata",
    "DocumentMetadataCodec": ".metadata",
    "EntitySpec": ".ontology",
    "Eq": ".filters",
    "FilterOp": ".filters",
    "Gte": ".filters",
    "RagPlugin": ".plugin",
    "RagPluginRegistry": ".registry",
    "Ontology": ".ontology",
    "RelationSpec": ".ontology",
    "Lte": ".filters",
}


def __getattr__(name: str) -> Any:
    """延迟加载 Facade 导出，避免基础模型加载时反向触发运行时装配。"""
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    return getattr(import_module(module_name, __name__), name)
