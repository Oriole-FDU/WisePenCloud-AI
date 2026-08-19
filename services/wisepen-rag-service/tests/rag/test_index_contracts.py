from dataclasses import replace

import pytest
from common.utils.document import SourceSpan

from rag.application.rag.acl import PermissionAuthorizer
from rag.application.rag.index.constructor import (
    build_content_revision_id,
    build_document_structure,
    build_reading_blocks,
    build_retrieval_chunks,
    build_source_refs,
    create_content_revision,
)
from rag.application.rag.navigate import (
    EvidenceCorruptError,
    EvidenceNotFoundError,
    EvidenceRevisionError,
    GraphEvidenceVerifier,
    SourceEvidenceVerifier,
)
from rag.application.rag.read import (
    ContentNotFoundError,
    DocumentContentReader,
    DocumentOutlineReader,
)
from rag.core.persistence.mongo.resource_index_writer import (
    _decide_stage,
    _ResourceIndexState,
)
from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.domain.models.content import ReadingBlock
from rag.domain.models.graph import GraphEvidence
from rag.domain.models.provenance import SourceEvidence, SourceRef
from rag.domain.models.retrieval import RetrievalCandidate, RetrievalChunk
from rag.domain.models.structure import Section
from rag.domain.repositories import PublishedResourceRevisionError, StageAction
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedGraphEvidence,
)


def _revision(markdown: str = "# 标题\n\n正文🙂。"):
    return create_content_revision(
        resource_id="resource-1",
        document_version=1,
        markdown=markdown,
    )


def test_revision_identity_and_unicode_length_are_stable() -> None:
    markdown = "# 标题\n\n正文🙂。"
    revision = _revision(markdown)
    structure = build_document_structure(markdown)
    assert revision.content_revision == _revision().content_revision
    assert not hasattr(revision, "total_length")
    assert structure.total_length == len(markdown)
    assert revision.content_hash
    assert revision.index_schema_version == "rag-v2-content:v6"


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        (None, StageAction.STAGED),
        (
            _ResourceIndexState(
                resource_id="resource-1",
                applied_content_revision="same",
                applied_document_version=1,
            ),
            StageAction.ALREADY_APPLIED,
        ),
        (
            _ResourceIndexState(
                resource_id="resource-1",
                applied_content_revision="newer",
                applied_document_version=2,
            ),
            StageAction.STALE,
        ),
    ],
)
def test_stage_decision(state, expected) -> None:
    revision = _revision()
    if state is not None and state.applied_content_revision == "same":
        state.applied_content_revision = revision.content_revision
    assert _decide_stage(revision, state) is expected


def test_same_document_version_with_corrected_content_is_staged() -> None:
    original = _revision("原文")
    corrected = _revision("修正后的原文")
    state = _ResourceIndexState(
        resource_id="resource-1",
        applied_content_revision=original.content_revision,
        applied_document_version=1,
    )
    assert _decide_stage(corrected, state) is StageAction.STAGED


def test_source_span_contract_uses_half_open_offsets() -> None:
    assert SourceSpan(1, 3).end_offset - SourceSpan(1, 3).start_offset == 2


class _MissingReader:
    async def get_document_outline(self, resource_id):
        return None

    async def get_pages(self, resource_id, page_labels):
        return None

    async def get_sections(self, resource_id, section_ids):
        return None


class _ReadableAclReader:
    async def get_resource_acl(self, resource_id):
        return ResourceAcl(
            resource_id=resource_id,
            acl_revision=1,
            owner_id="user-1",
        )

    async def get_resource_acls(self, resource_ids):
        return {
            resource_id: ResourceAcl(
                resource_id=resource_id,
                acl_revision=1,
                owner_id="user-1",
            )
            for resource_id in resource_ids
        }


class _DeniedAclReader:
    async def get_resource_acl(self, resource_id):
        return None

    async def get_resource_acls(self, resource_ids):
        return {}


@pytest.mark.asyncio
async def test_read_actions_raise_directly_when_content_is_missing() -> None:
    reader = _MissingReader()
    authorizer = PermissionAuthorizer(local_store=_ReadableAclReader())
    scope = PermissionScope(user_id="user-1")
    with pytest.raises(ContentNotFoundError):
        await DocumentOutlineReader(
            structure_reader=reader, authorizer=authorizer
        ).get_document_outline(
            resource_id="missing",
            permission_scope=scope,
        )
    with pytest.raises(ContentNotFoundError):
        await DocumentContentReader(reader=reader, authorizer=authorizer).get_pages(
            resource_id="missing",
            page_labels=["1"],
            permission_scope=scope,
        )
    with pytest.raises(ContentNotFoundError):
        await DocumentContentReader(reader=reader, authorizer=authorizer).get_sections(
            resource_id="missing",
            section_ids=["section"],
            permission_scope=scope,
        )


@pytest.mark.asyncio
async def test_read_actions_do_not_distinguish_denied_resource_from_missing() -> None:
    reader = _MissingReader()
    authorizer = PermissionAuthorizer(local_store=_DeniedAclReader())
    scope = PermissionScope(user_id="user-1")

    with pytest.raises(ContentNotFoundError):
        await DocumentOutlineReader(
            structure_reader=reader, authorizer=authorizer
        ).get_document_outline(
            resource_id="private-resource",
            permission_scope=scope,
        )


def _evidence_facts() -> tuple[
    str, object, list[ReadingBlock], list[RetrievalChunk], list[SourceRef], object
]:
    markdown = "# 标题\n\n正文内容。"
    revision_id = build_content_revision_id(
        resource_id="resource-1", document_version=1, markdown=markdown
    )
    structure = build_document_structure(markdown)
    blocks = build_reading_blocks(
        resource_id="resource-1",
        content_revision=revision_id,
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
    )
    chunks = build_retrieval_chunks(
        markdown=markdown,
        structure=structure,
        sections=structure.sections,
        reading_blocks=blocks,
    )
    refs = build_source_refs(
        resource_id="resource-1",
        content_revision=revision_id,
        retrieval_chunks=chunks,
    )
    revision = create_content_revision(
        resource_id="resource-1",
        document_version=1,
        markdown=markdown,
    )
    return markdown, structure, blocks, chunks, refs, revision


class _PublishedResourceReader:
    def __init__(self, record: SourceEvidence) -> None:
        self.record = record

    async def get_source_evidence(
        self,
        resource_id,
        content_revision,
        source_ref_ids,
    ):
        if resource_id != self.record.source_ref.resource_id:
            return None
        if content_revision != self.record.source_ref.content_revision:
            raise PublishedResourceRevisionError(content_revision)
        return {self.record.source_ref.ref_id: self.record}


@pytest.mark.asyncio
async def test_verifier_accepts_authoritative_retrieval_candidate() -> None:
    markdown, structure, blocks, chunks, refs, revision = _evidence_facts()
    record = SourceEvidence(
        source_ref=refs[0],
        reading_block=blocks[0],
        section=next(
            section
            for section in structure.sections
            if section.section_id == refs[0].section_id
        ),
        source_text=markdown[
            refs[0].source_spans[0].start_offset : refs[0].source_spans[0].end_offset
        ],
    )
    verified = await SourceEvidenceVerifier(
        reader=_PublishedResourceReader(record)
    ).verify_retrieval_candidates(
        [_retrieval_candidate(chunks[0], refs[0], revision)],
    )
    assert verified == [record]


@pytest.mark.asyncio
async def test_verifier_rejects_missing_ref_and_wrong_chunk() -> None:
    _, structure, blocks, chunks, refs, revision = _evidence_facts()
    record = SourceEvidence(
        source_ref=refs[0],
        reading_block=blocks[0],
        section=next(
            section
            for section in structure.sections
            if section.section_id == refs[0].section_id
        ),
        source_text=chunks[0].raw_text,
    )
    reader = _PublishedResourceReader(record)
    candidate = _retrieval_candidate(chunks[0], refs[0], revision)
    missing = replace(candidate, source_ref_id="missing")
    with pytest.raises(EvidenceNotFoundError):
        await SourceEvidenceVerifier(reader=reader).verify_retrieval_candidates(
            [missing]
        )

    wrong_chunk = replace(candidate, chunk_id="wrong")
    with pytest.raises(EvidenceCorruptError):
        await SourceEvidenceVerifier(reader=reader).verify_retrieval_candidates(
            [wrong_chunk],
        )

    stale = replace(candidate, content_revision="stale")
    with pytest.raises(EvidenceRevisionError):
        await SourceEvidenceVerifier(reader=reader).verify_retrieval_candidates([stale])


@pytest.mark.parametrize(
    "changes",
    [
        {"reading_block_id": "wrong-block"},
        {"section_id": "wrong-section"},
        {"section_path": ["wrong-section"]},
        {"source_spans": [SourceSpan(0, 1)]},
        {"page_labels": ["wrong-page"]},
        {"anchor_labels": ["wrong-anchor"]},
        {"raw_text": "wrong-text"},
    ],
)
@pytest.mark.asyncio
async def test_verifier_rejects_candidate_identity_or_text_drift(changes) -> None:
    _, structure, blocks, chunks, refs, revision = _evidence_facts()
    record = SourceEvidence(
        source_ref=refs[0],
        reading_block=blocks[0],
        section=next(
            section
            for section in structure.sections
            if section.section_id == refs[0].section_id
        ),
        source_text=chunks[0].raw_text,
    )
    candidate = replace(
        _retrieval_candidate(chunks[0], refs[0], revision),
        **changes,
    )

    with pytest.raises(EvidenceCorruptError):
        await SourceEvidenceVerifier(
            reader=_PublishedResourceReader(record)
        ).verify_retrieval_candidates([candidate])


@pytest.mark.asyncio
async def test_graph_verifier_preserves_graph_evidence_identity_and_order() -> None:
    evidence = GraphEvidence(
        evidence_id="evidence-1",
        resource_id="resource-1",
        content_revision="revision-1",
        reading_block_id="block-1",
        source_span=SourceSpan(0, 4),
        quote="正文内容",
    )
    span = SourceSpan(0, 4)
    block = ReadingBlock(
        block_id="block-1",
        section_ids=["section-1"],
        ordinal=0,
        raw_text="正文内容",
        source_spans=[span],
    )
    section = Section(
        section_id="section-1",
        title="标题",
        level=1,
        parent_section_id=None,
        ordinal=0,
        section_path=("标题",),
        own_span=span,
        subtree_span=span,
    )
    record = PublishedGraphEvidence(evidence, block, section, span)

    class _Reader:
        async def get_graph_evidence(self, resource_id, content_revision, items):
            return {evidence.evidence_id: record}

    verified = await GraphEvidenceVerifier(reader=_Reader()).verify([evidence])

    assert verified == [record]


def _retrieval_candidate(
    chunk: RetrievalChunk,
    source_ref: SourceRef,
    revision,
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk.chunk_id,
        reading_block_id=chunk.reading_block_id,
        section_id=chunk.section_id,
        section_path=list(chunk.section_path),
        resource_id=revision.resource_id,
        content_revision=revision.content_revision,
        raw_text=chunk.raw_text,
        source_spans=list(chunk.source_spans),
        page_labels=list(chunk.page_labels),
        anchor_labels=list(chunk.anchor_labels),
        source_ref_id=source_ref.ref_id,
        score=1.0,
    )
