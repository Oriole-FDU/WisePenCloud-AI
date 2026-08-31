"""编排一个资源 revision 的构建与跨后端发布。

ResourceIndexer 是 INDEX 流水线的顶层入口：把一份权威 Markdown 落库到资源元数据
和检索后端；知识图谱是否生成和写入由显式 settings 开关决定。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.utils.document import OutlineAssembler

from rag.application.rag.acl import ResourceAclRefresher
from rag.domain.models.structure import StructureMode
from rag.domain.repositories import (
    KnowledgeGraphRepository,
    ResourceAclStore,
    ResourceIndexWriter,
    RetrievalIndexWriter,
    StageAction,
)

from .constructor import (
    build_content_revision_id,
    build_document_structure,
    build_reading_blocks,
    build_retrieval_chunks,
    build_source_refs,
    create_content_revision,
)
from .contextualize import ContextualTextIndexer
from .graph import KnowledgeGraphExtractor, merge_candidate_graph

if TYPE_CHECKING:
    from rag.utils.llm_clients import EmbeddingClient


class ResourceIndexer:
    """把权威 Markdown 发布为可读、可检索、可探索的同一 revision。

    设计要点：
    - 派生身份全部由 content_revision 派生，重复索引同一内容会得到完全一致的 ID，
      支持增量复用与缓存命中。
    - 写入流程分阶段（stage → apply → activate → cleanup），中途失败不会污染线上版本。
    - 检索索引与知识图谱解耦：FLAT_TEXT/EMPTY 不抽取图谱，但仍会发布检索索引。
    """

    def __init__(
        self,
        *,
        contextual_text: ContextualTextIndexer,
        embedding_client: EmbeddingClient,
        acl_refresher: ResourceAclRefresher,
        acl_reader: ResourceAclStore,
        resource_writer: ResourceIndexWriter,
        retrieval_writer: RetrievalIndexWriter,
        graph_extractor: KnowledgeGraphExtractor,
        graph_repository: KnowledgeGraphRepository,
        contextual_enabled: bool = False,
        graph_enabled: bool = False,
    ) -> None:
        self._contextual_text = contextual_text
        self._embedding_client = embedding_client
        self._acl_refresher = acl_refresher
        self._acl_reader = acl_reader
        self._resource_writer = resource_writer
        self._retrieval_writer = retrieval_writer
        self._graph_extractor = graph_extractor
        self._graph_repository = graph_repository
        self._contextual_enabled = contextual_enabled
        self._graph_enabled = graph_enabled

    async def index_resource(
        self,
        *,
        resource_id: str,
        document_version: int,
        markdown: str,
    ) -> StageAction:
        """对一份权威 Markdown 执行完整的索引构建与发布。"""
        # 派生身份与结构
        # 先用 markdown 计算 content_revision_id（不依赖 structure），供后续派生 ID 使用。
        content_revision_id = build_content_revision_id(
            resource_id=resource_id,
            document_version=document_version,
            markdown=markdown,
        )
        structure = build_document_structure(markdown)
        outline = OutlineAssembler().assemble(
            sections=structure.sections,
            pages=structure.pages,
            anchors=structure.anchors,
        )
        revision = create_content_revision(
            resource_id=resource_id,
            document_version=document_version,
            markdown=markdown,
        )
        # sections 已按模式在 Common DocumentChunker 中构建完成。
        sections = structure.sections
        reading_blocks = build_reading_blocks(
            resource_id=resource_id,
            content_revision=content_revision_id,
            markdown=markdown,
            structure=structure,
            sections=sections,
        )
        chunks = build_retrieval_chunks(
            markdown=markdown,
            structure=structure,
            sections=sections,
            reading_blocks=reading_blocks,
        )
        source_refs = build_source_refs(
            resource_id=resource_id,
            content_revision=content_revision_id,
            retrieval_chunks=chunks,
        )

        # 资源元数据暂存
        # STALE 表示该 revision 与已上线版本完全一致，跳过下游步骤。
        action = await self._resource_writer.stage_revision(
            revision=revision,
            markdown=markdown,
            structure=structure,
            outline=outline,
            reading_blocks=reading_blocks,
            source_refs=source_refs,
        )
        if action is StageAction.STALE:
            return action

        # 上下文增强（可关闭的 LLM 旁路）
        # 为每个 chunk 生成检索上下文，提升 dense/BM25 召回；已缓存的内容不会重复调用模型。
        # 关闭时不重写 index_text，沿用构造时的 index_text=raw_text。
        if self._contextual_enabled:
            chunks = await self._contextual_text.contextualize(
                resource_id=resource_id,
                structure=structure,
                reading_blocks=reading_blocks,
                chunks=chunks,
            )

        # 向量计算
        # 先尝试复用已存储的向量（chunk_id 一致即可复用），缺失部分才调用 embedding 模型。
        dense_vectors = dict(
            await self._retrieval_writer.load_reusable_vectors(
                resource_id=resource_id,
                chunks=chunks,
            )
        )
        missing_chunks = [
            chunk for chunk in chunks if chunk.chunk_id not in dense_vectors
        ]
        if missing_chunks:
            result = await self._embedding_client.aembed(
                [chunk.index_text for chunk in missing_chunks]
            )
            # 防御：保证 embedding 数量与输入一致，避免向量与 chunk 错位。
            if len(result.embeddings) != len(missing_chunks):
                raise ValueError(
                    "embedding response count does not match retrieval chunks"
                )
            dense_vectors.update(
                {
                    chunk.chunk_id: vector
                    for chunk, vector in zip(
                        missing_chunks,
                        result.embeddings,
                        strict=True,
                    )
                }
            )

        # ACL 同步

        # 显式刷新 ACL，避免在索引期间资源 ACL 发生变更导致检索结果不一致。
        await self._acl_refresher.refresh(resource_id)
        resource_acl = await self._acl_reader.get_resource_acl(resource_id)
        # 检索结果必须按 ACL 过滤；若资源尚无 ACL 则视为配置错误，直接报错。
        if resource_acl is None:
            raise RuntimeError(f"resource {resource_id} has no synchronized ACL")

        # 检索索引发布
        # write_staged_revision 写入暂存；apply_revision + activate_revision 才让线上可见。
        await self._retrieval_writer.write_staged_revision(
            resource_id=resource_id,
            content_revision=content_revision_id,
            chunks=chunks,
            source_refs=source_refs,
            dense_vectors=dense_vectors,
            resource_acl=resource_acl,
        )
        await self._resource_writer.apply_revision(revision)
        await self._retrieval_writer.activate_revision(
            resource_id=resource_id,
            content_revision=content_revision_id,
        )
        # 清理检索后端的旧 revision，避免历史版本累积。
        await self._retrieval_writer.delete_other_revisions(
            resource_id=resource_id,
            keep_content_revision=content_revision_id,
        )

        # 知识图谱旁路
        # 该开关只控制索引生成/写入，不影响 graph expand 的接口装配；关闭时保留已有图数据。
        if self._graph_enabled:
            if structure.mode is StructureMode.SECTIONED:
                await self._graph_repository.begin_build(
                    resource_id=resource_id,
                    content_revision=content_revision_id,
                    document_version=document_version,
                )
                graph = merge_candidate_graph(
                    resource_id=resource_id,
                    content_revision=content_revision_id,
                    extractions=await self._graph_extractor.extract(
                        resource_id=resource_id,
                        content_revision=content_revision_id,
                    ),
                )
                await self._graph_repository.publish(
                    graph=graph,
                    document_version=document_version,
                )
            else:
                await self._graph_repository.skip(
                    resource_id=resource_id,
                    content_revision=content_revision_id,
                    document_version=document_version,
                )

        # 资源元数据清理
        # 资源后端也清理旧 revision，使线上资源元数据只保留最新版本。
        await self._resource_writer.delete_other_revisions(
            resource_id=resource_id,
            keep_content_revision=content_revision_id,
        )
        return action
