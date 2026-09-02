"""启动期图谱插件注册、运行时路由与 metadata 编解码装配。"""

from rag.application.document.models import DocChunk, Document
from rag.application.plugins.core.codecs import (
    DocChunkMetadataCodec,
    DocumentMetadataCodec,
)
from rag.application.plugins.core.models import (
    DocChunkMetadata,
    DocumentMetadata,
    GeneralChunkMetadata,
)
from rag.application.plugins.core.plugin import ChunkMetadataBuilder, GraphPlugin


class DocumentChunkMetadataBuilder:
    """按 DocumentMetadata 类型路由垂类 Chunk metadata 生产器。"""

    def __init__(self, *, builders: list[ChunkMetadataBuilder] | None = None) -> None:
        builders = builders or []
        self._builders = {
            builder.doc_metadata_type: builder
            for builder in builders
        }
        if len(self._builders) != len(builders):
            raise ValueError("document metadata can have only one chunk metadata builder")

    @property
    def metadata_types(self) -> list[type[DocChunkMetadata]]:
        """返回持久化仓储恢复多态 metadata 所需的类型。"""
        return [builder.chunk_metadata_type for builder in self._builders.values()]

    def build_metadata(
        self,
        *,
        document: Document,
        chunk: DocChunk,
    ) -> DocChunkMetadata:
        """
        运行时自动分配对应的metadata builder，构造垂类 Chunk metadata；
        没有匹配垂类规则时保持通用 metadata。
        """
        builder = self._builders.get(type(document.metadata))
        if builder is None:
            return GeneralChunkMetadata()
        return builder.build_metadata(document=document, chunk=chunk)


class GraphPluginRegistry:
    """不可变的垂类插件组合结果，供准备、持久化和图谱用例共享。"""

    def __init__(self, *, plugins: list[GraphPlugin] | None = None) -> None:
        plugins = plugins or []
        self._plugins_by_id = {plugin.plugin_id: plugin for plugin in plugins}
        if len(self._plugins_by_id) != len(plugins):
            raise ValueError("graph plugin ids must be unique")

        metadata_types = [plugin.metadata_type for plugin in plugins]
        self.document_metadata_codec = DocumentMetadataCodec(metadata_types)

        builders = [
            plugin.chunk_metadata_builder
            for plugin in plugins
            if plugin.chunk_metadata_builder is not None
        ]
        self.chunk_metadata_builder = DocumentChunkMetadataBuilder(builders=builders)
        self.doc_chunk_metadata_codec = DocChunkMetadataCodec(
            self.chunk_metadata_builder.metadata_types
        )
        self._plugins_by_metadata_type = {
            plugin.metadata_type: plugin for plugin in plugins
        }
        if len(self._plugins_by_metadata_type) != len(plugins):
            raise ValueError("graph plugin metadata types must be unique")

    def match_document(self, metadata: DocumentMetadata) -> GraphPlugin | None:
        """返回负责该文档 metadata 的垂类插件。"""
        return self._plugins_by_metadata_type.get(type(metadata))

    def get(self, plugin_id: str) -> GraphPlugin | None:
        """按调用方声明的插件身份定位插件。"""
        return self._plugins_by_id.get(plugin_id)
