"""垂类插件 SPI Facade；垂类实现只依赖此模块。"""

from importlib import import_module
from typing import Any

__all__ = [
    "ChunkMetadataBuilder",
    "DeclarativeGraphFilter",
    "DeterministicGraphProducer",
    "DocChunkMetadata",
    "DocumentMetadata",
    "EntitySpec",
    "Eq",
    "FilterOp",
    "GraphPlugin",
    "GraphPluginRegistry",
    "Gte",
    "Lte",
    "Ontology",
    "RelationSpec",
]

_EXPORT_MODULES = {
    "ChunkMetadataBuilder": ".plugin",
    "DeterministicGraphProducer": ".plugin",
    "DeclarativeGraphFilter": ".filters",
    "DocChunkMetadata": ".models",
    "DocumentMetadata": ".models",
    "EntitySpec": ".ontology",
    "Eq": ".filters",
    "FilterOp": ".filters",
    "Gte": ".filters",
    "GraphPlugin": ".plugin",
    "GraphPluginRegistry": ".registry",
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
