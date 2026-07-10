from __future__ import annotations

from .block_packers.size_bounded_block_packer import SizeBoundedBlockPacker, SizeBoundedBlockPackerConfig
from .block_splitters.markdown_block_splitter import MarkdownBlockSplitter
from .block_splitters.recursive_text_block_splitter import RecursiveTextBlockSplitter, RecursiveTextBlockSplitterConfig
from .chunk_derivers.child_chunk_deriver import ChildChunkDeriver, ChildChunkDeriverConfig
from .chunk_locators.markdown_chunk_locator import MarkdownChunkLocator
from .chunk_normalizers.flat_chunk_normalizer import FlatChunkNormalizer
from .chunk_normalizers.parent_child_chunk_normalizer import ParentChildChunkNormalizer
from .engine import ChunkingEngine
from .models import ChunkRole
from .pipeline import ChunkingPipeline

DEFAULT_CHUNK_SIZE: int = 6000


class ChunkingEngineRegistry:
    """按名称提供已注册的 ChunkingEngine 单例。"""

    __slots__ = ("_engines",)

    def __init__(self) -> None:
        self._engines = {
            "markdown": ChunkingEngine(
                pipeline=ChunkingPipeline(
                    name="markdown",
                    block_splitter=MarkdownBlockSplitter(),
                    block_packer=SizeBoundedBlockPacker(
                        SizeBoundedBlockPackerConfig(
                            chunk_size=DEFAULT_CHUNK_SIZE,
                            role=ChunkRole.FLAT,
                            split_on_page_markers=True,
                        )
                    ),
                    chunk_normalizers=(
                        FlatChunkNormalizer(
                            respect_page_boundaries=True,
                        ),
                    ),
                    chunk_locator=MarkdownChunkLocator(),
                )
            ),
            "plain_text": ChunkingEngine(
                pipeline=ChunkingPipeline(
                    name="plain_text",
                    # 无 Markdown 结构时使用；不产出 section/page/anchor 语义索引。
                    block_splitter=RecursiveTextBlockSplitter(
                        RecursiveTextBlockSplitterConfig(
                            chunk_size=DEFAULT_CHUNK_SIZE,
                            chunk_overlap=0,
                        )
                    ),
                    chunk_normalizers=(FlatChunkNormalizer(),),
                )
            ),
            "markdown_recursive": ChunkingEngine(
                pipeline=ChunkingPipeline(
                    name="markdown_recursive",
                    # 结构块过大时兜底递归切分；更偏文本长度，不保证 Markdown block 完整。
                    block_splitter=RecursiveTextBlockSplitter(
                        RecursiveTextBlockSplitterConfig.for_markdown(
                            chunk_size=DEFAULT_CHUNK_SIZE,
                            chunk_overlap=0,
                        )
                    ),
                    chunk_normalizers=(FlatChunkNormalizer(),),
                    chunk_locator=MarkdownChunkLocator(),
                )
            ),
            "parent_child_markdown": ChunkingEngine(
                pipeline=ChunkingPipeline(
                    name="parent_child_markdown",
                    block_splitter=MarkdownBlockSplitter(),
                    block_packer=SizeBoundedBlockPacker(
                        SizeBoundedBlockPackerConfig(
                            chunk_size=DEFAULT_CHUNK_SIZE,
                            role=ChunkRole.PARENT,
                            # RAG 父子块首选；父块不跨页，子块继承父块页码 metadata。
                            split_on_page_markers=True,
                        )
                    ),
                    chunk_derivers=(
                        ChildChunkDeriver(
                            ChildChunkDeriverConfig(
                                child_chunk_size=600,
                                child_overlap=100,
                            )
                        ),
                    ),
                    chunk_normalizers=(
                        ParentChildChunkNormalizer(
                            respect_page_boundaries=True,
                        ),
                    ),
                    chunk_locator=MarkdownChunkLocator(),
                )
            ),
        }

    def get(self, name: str) -> ChunkingEngine:
        try:
            return self._engines[name]
        except KeyError as exc:
            raise KeyError(f"Unknown chunking engine: {name!r}") from exc


_REGISTRY = ChunkingEngineRegistry()


def get_chunking_engine(name: str) -> ChunkingEngine:
    return _REGISTRY.get(name)
