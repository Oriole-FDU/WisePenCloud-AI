from __future__ import annotations

from .extra_indexers.chunk_extral_indexer import ChunkExtraIndexer
from .models import ChunkLevel
from .packers.block_aware_packer import BlockAwarePacker, BlockAwarePackerConfig
from .pipeline import ChunkingPipeline
from .post_processors.secondary_chunk_finalizer import SecondaryChunkFinalizer
from .post_processors.secondary_chunk_processor import SecondaryChunkConfig, SecondaryChunkProcessor
from .post_processors.single_layer_finalizer import SingleLayerFinalizer
from .pre_processors.markdown_pre_processor import MarkdownPreProcessor
from .splitters.markdown_block_splitter import MarkdownBlockSplitter
from .splitters.recursive_text_splitter import RecursiveTextSplitter, RecursiveTextSplitterConfig

DEFAULT_CHUNK_SIZE: int = 4000
MARKDOWN_PIPELINE_NAME = "markdown"
PLAIN_TEXT_PIPELINE_NAME = "plain_text"
MARKDOWN_RECURSIVE_PIPELINE_NAME = "markdown_recursive"
NESTED_MARKDOWN_PIPELINE_NAME = "nested_markdown"


class ChunkingPresetRegistry:
    """按名字提供预置分块 pipeline，内部维护单例实例。"""

    __slots__ = ("_pipelines",)

    def __init__(self) -> None:
        self._pipelines = {
            MARKDOWN_PIPELINE_NAME: ChunkingPipeline(
                name=MARKDOWN_PIPELINE_NAME,
                splitter=MarkdownBlockSplitter(),
                packer=BlockAwarePacker(
                    BlockAwarePackerConfig(chunk_size=DEFAULT_CHUNK_SIZE, level=ChunkLevel.READ)
                ),
                pre_processors=(MarkdownPreProcessor(),),
                post_processors=(SingleLayerFinalizer(),),
                extra_indexer=ChunkExtraIndexer(),
            ),
            PLAIN_TEXT_PIPELINE_NAME: ChunkingPipeline(
                name=PLAIN_TEXT_PIPELINE_NAME,
                splitter=RecursiveTextSplitter(
                    RecursiveTextSplitterConfig(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=0)
                ),
                pre_processors=(),
                post_processors=(SingleLayerFinalizer(),),
            ),
            MARKDOWN_RECURSIVE_PIPELINE_NAME: ChunkingPipeline(
                name=MARKDOWN_RECURSIVE_PIPELINE_NAME,
                splitter=RecursiveTextSplitter(
                    RecursiveTextSplitterConfig.for_markdown(chunk_size=DEFAULT_CHUNK_SIZE, chunk_overlap=0)
                ),
                pre_processors=(MarkdownPreProcessor(),),
                post_processors=(SingleLayerFinalizer(),),
                extra_indexer=ChunkExtraIndexer(),
            ),
            NESTED_MARKDOWN_PIPELINE_NAME: ChunkingPipeline(
                name=NESTED_MARKDOWN_PIPELINE_NAME,
                splitter=MarkdownBlockSplitter(),
                packer=BlockAwarePacker(
                    BlockAwarePackerConfig(chunk_size=DEFAULT_CHUNK_SIZE, level=ChunkLevel.RETRIEVAL)
                ),
                pre_processors=(MarkdownPreProcessor(),),
                post_processors=(
                    SecondaryChunkProcessor(SecondaryChunkConfig(child_chunk_size=600, child_overlap=100)),
                    SecondaryChunkFinalizer(),
                ),
            ),
        }

    def get(self, name: str) -> ChunkingPipeline:
        try:
            return self._pipelines[name]
        except KeyError as exc:
            raise ValueError(f"Unknown chunking pipeline: {name}") from exc


_REGISTRY = ChunkingPresetRegistry()


def get_chunking_pipeline(name: str) -> ChunkingPipeline:
    return _REGISTRY.get(name)
