"""为各 contract demo 构造中性、可回源的真实索引产物。"""

from dataclasses import dataclass

from rag.application.rag.index.constructor import (
    build_content_revision_id,
    build_reading_blocks,
    build_retrieval_chunks,
    build_source_refs,
    create_content_revision,
    build_document_structure,
)
from rag.domain.models.content import ContentRevision, ReadingBlock
from rag.domain.models.provenance import SourceRef
from rag.domain.models.retrieval import RetrievalChunk
from rag.domain.models.structure import DocumentStructure, Section, StructureMode


@dataclass(slots=True)
class DemoDocument:
    """一份 demo 文档及生产索引构造器派生出的完整事实。"""

    resource_id: str
    markdown: str
    revision: ContentRevision
    structure: DocumentStructure
    sections: list[Section]
    reading_blocks: list[ReadingBlock]
    retrieval_chunks: list[RetrievalChunk]
    source_refs: list[SourceRef]


def build_demo_document(*, resource_id: str, markdown: str) -> DemoDocument:
    """执行 ResourceIndexer 中内容派生阶段的同一组生产算法。"""
    content_revision = build_content_revision_id(
        resource_id=resource_id,
        document_version=1,
        markdown=markdown,
    )
    structure = build_document_structure(markdown)
    revision = create_content_revision(
        resource_id=resource_id,
        document_version=1,
        markdown=markdown,
    )
    # sections 已按模式在 Common DocumentChunker 中构建完成。
    sections = structure.sections
    reading_blocks = build_reading_blocks(
        resource_id=resource_id,
        content_revision=content_revision,
        markdown=markdown,
        structure=structure,
        sections=sections,
    )
    retrieval_chunks = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=sections,
        reading_blocks=reading_blocks,
    )
    source_refs = build_source_refs(
        resource_id=resource_id,
        content_revision=content_revision,
        retrieval_chunks=retrieval_chunks,
    )
    return DemoDocument(
        resource_id=resource_id,
        markdown=markdown,
        revision=revision,
        structure=structure,
        sections=sections,
        reading_blocks=reading_blocks,
        retrieval_chunks=retrieval_chunks,
        source_refs=source_refs,
    )


def sectioned_markdown() -> str:
    return """版本号 v2.3，发布日期 2026-08-01。本手册供内部巡检人员使用。

# WisePen RAG 导航架构说明

WisePen RAG 面向企业文档问答场景，使用向量检索和结构化文档导航共同组织模型可读上下文。

<!-- page 1 -->

## 一、主检索流程

WisePen RAG 使用向量检索召回相关 ReadingBlock，并按照标题树补充结构上相邻的阅读材料。

## 二、图谱导航

WisePen RAG 使用 GraphRAG 技术补充实体关系导航，使模型能够沿文档中的知识关系继续读取材料。

### 二.1 关系抽取

GraphRAG 使用知识图谱表示实体之间的关系，并保留关系对应的原文证据。

<!-- page 2 -->

### 二.2 证据回源

图谱关系只能作为导航线索，最终返回的证据仍然必须回到当前发布 revision 的 ReadingBlock。

### 二.3 查询边界

图谱遍历只使用显式知识关系，不把节点 mention 或检索来源边当作可遍历关系。

## 三、响应编排

导航响应区分 seed 节点、新发现节点、关系证据和节点 mention 证据，并将同一 ReadingBlock 去重后返回。
"""


def flat_text_markdown() -> str:
    return """果园霜冻观测记录，四月十二日。傍晚十八时，坡底测点温度为六点八摄氏度，坡肩测点为八点一摄氏度，风速低于每秒一米。天空晴朗，地表长波辐射散失明显，值班人员因此将本夜判断为辐射降温风险较高。
二十二时三十分，坡底温度降至三点二摄氏度，叶面仍未结霜。工作人员检查风机燃油、转向和警戒范围，并确认灌溉主管无渗漏。按照预案，风机不因单个测点短时下降立即启动，而要结合坡底与坡肩温差、露点和连续下降趋势判断。
次日零时四十分，坡底与坡肩温差扩大到三点四摄氏度，近地层出现稳定逆温。风机于零时四十五分启动，每二十分钟复测一次。启动后坡底温度回升约零点七摄氏度，叶面未发现冰晶。
日出前后最容易出现当夜最低温。值班人员在五时二十分停止风机前，先确认东方云量增加、坡底温度连续三次回升，并保留一组未干预地块作为对照。最终记录应注明仪器编号、校准日期和缺测时段，避免把设备漂移误判为防霜措施效果。
"""
