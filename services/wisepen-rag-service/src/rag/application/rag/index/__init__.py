"""资源索引构建的对外入口。

对外暴露三个核心组件：
- ResourceIndexer：编排一个资源 revision 的完整索引构建与发布流程。
- ContextualTextIndexer：为 RetrievalChunk 生成上下文，增强 index_text。
- KnowledgeGraphExtractor：抽取并校验窗口级知识图谱候选。
"""

from .contextualize import ContextualTextIndexer
from .graph import KnowledgeGraphExtractor
from .resource_deleter import ResourceDeleter
from .resource_indexer import ResourceIndexer

__all__ = [
    "ContextualTextIndexer",
    "KnowledgeGraphExtractor",
    "ResourceDeleter",
    "ResourceIndexer",
]
