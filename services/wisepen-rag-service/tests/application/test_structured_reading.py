"""目录与章节读取共用真实事实、快照和 ID；正文在授权后按原文范围返回。"""

import asyncio
import re
from dataclasses import fields, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from common.utils.markdown import MarkdownChunker, OutlineFormatter, SourceSpan
from pydantic import ValidationError

from rag.application.acl.models import PermissionScope, ResourceAcl
from rag.application.common.snapshot import ResourceSnapshot
from rag.application.reading import DocumentReading, SectionReadScope
from rag.core.schema import ContentRevision
from rag.core.schema.content import rag_section_id
from rag.ingestion.chunkers import MarkdownChunker as RagMarkdownChunker

SOURCE = (
    "文档开头😀\n\n"
    "# 第一章\n\n  直属原文。\n\n"
    "### 数据\n\n表 1: 数据\n\n| 值 |\n| --- |\n| 42 |\n\n"
    "#### 细节\n\n保留  两个空格。\n\n"
    "### 邻居\n\n邻居正文。\n\n"
    "# 第二章\n\n末尾原文。\n"
)


@pytest.fixture
async def reading_setup(
    make_read_document, publish_read_document, read_acls, snapshot_reader
):
    document = await publish_read_document(make_read_document(text=SOURCE))
    read_acls[document.resource_id] = ResourceAcl(document.resource_id, 1, "reader")
    return SimpleNamespace(
        document=document,
        snapshot=ResourceSnapshot(PermissionScope("reader")),
        usecase=DocumentReading(reader=snapshot_reader),
    )


async def test_ingestion_ids_and_parent_links_are_shared_with_chunks(reading_setup):
    document = reading_setup.document
    common = MarkdownChunker().chunk(document.text)
    ids = {
        section.section_id: rag_section_id(
            resource_id=document.resource_id,
            content_revision=document.content_revision,
            common_section_id=section.section_id,
        )
        for section in common.sections
    }
    for actual, original in zip(
        document.structure.sections, common.sections, strict=True
    ):
        assert actual == replace(
            original,
            section_id=ids[original.section_id],
            parent_section_id=(
                ids[original.parent_section_id]
                if original.parent_section_id is not None
                else None
            ),
        )
    chunks = await RagMarkdownChunker().split(document)
    assert {chunk.section_id for chunk in chunks if chunk.section_id} <= set(
        ids.values()
    )
    assert document.structure.anchors == common.anchors


async def test_outline_ids_can_be_used_directly_for_neighborhood_and_reading(
    reading_setup,
):
    document, usecase, snapshot = (
        reading_setup.document,
        reading_setup.usecase,
        reading_setup.snapshot,
    )
    formatter = OutlineFormatter(
        sections=document.structure.sections, anchors=document.structure.anchors
    )
    outline = await usecase.global_outline(document.resource_id, snapshot=snapshot)
    assert outline.outline == formatter.global_outline()
    assert outline.content_revision == document.content_revision
    ids = re.findall(r"id=(rsec_[a-f0-9]{24})", outline.outline)
    assert ids == [section.section_id for section in document.structure.sections]
    current = next(
        section for section in document.structure.sections if section.title == "数据"
    )
    neighbors = await usecase.neighborhood([current.section_id], snapshot=snapshot)
    assert neighbors[0].outline == formatter.neighborhood(current.section_id)
    assert "[C]" in neighbors[0].outline and "[Table 1]" in neighbors[0].outline
    assert neighbors[0].section_path == current.section_path
    limited = await usecase.global_outline(
        document.resource_id, snapshot=snapshot, max_level=1
    )
    assert limited.outline == formatter.global_outline(max_level=1)
    assert await usecase.read_sections(ids, snapshot=snapshot)


@pytest.mark.parametrize("scope", list(SectionReadScope))
async def test_section_text_is_exact_complete_original_span(reading_setup, scope):
    document = reading_setup.document
    result = await reading_setup.usecase.read_sections(
        [section.section_id for section in document.structure.sections],
        snapshot=reading_setup.snapshot,
        scope=scope,
    )
    for item, section in zip(result, document.structure.sections, strict=True):
        span = (
            section.own_span if scope is SectionReadScope.OWN else section.subtree_span
        )
        assert item.text == SOURCE[span.start_offset : span.end_offset]
        assert item.section_path == section.section_path
    assert {field.name for field in fields(result[0])} == {
        "resource_id",
        "content_revision",
        "section_id",
        "section_path",
        "text",
    }
    first_chapter = next(item for item in result if item.section_path == ["第一章"])
    assert first_chapter.text.startswith("# 第一章\n\n  直属原文。")
    assert ("### 数据" in first_chapter.text) == (scope is SectionReadScope.SUBTREE)


async def test_chunk_text_is_exact_original_and_location_with_fixed_fields(
    reading_setup, storage
):
    document, usecase, snapshot = (
        reading_setup.document,
        reading_setup.usecase,
        reading_setup.snapshot,
    )
    chunks = await RagMarkdownChunker().split(document)
    await storage.upsert_chunks(chunks)
    result = await usecase.read_chunks(
        [chunk.chunk_id for chunk in chunks], snapshot=snapshot
    )
    assert [item.chunk_id for item in result] == [chunk.chunk_id for chunk in chunks]
    for item, chunk in zip(result, chunks, strict=True):
        assert item.text == chunk.raw_text
        assert item.resource_id == chunk.resource_id
        assert item.content_revision == chunk.content_revision
        assert item.section_id == chunk.section_id
        assert item.section_path == chunk.section_path
    assert {field.name for field in fields(result[0])} == {
        "resource_id",
        "content_revision",
        "chunk_id",
        "section_id",
        "section_path",
        "text",
    }


async def test_chunk_reads_deduplicate_preserving_order_and_fetch_once(
    reading_setup, storage, make_read_chunk
):
    document, usecase, snapshot = (
        reading_setup.document,
        reading_setup.usecase,
        reading_setup.snapshot,
    )
    first, second = make_read_chunk(document, 0), make_read_chunk(document, 1)
    await storage.upsert_chunks([first, second])
    storage.get_chunks = AsyncMock(wraps=storage.get_chunks)
    requested = [second.chunk_id, first.chunk_id, second.chunk_id, first.chunk_id]
    result = await usecase.read_chunks(requested, snapshot=snapshot)
    assert [item.chunk_id for item in result] == [second.chunk_id, first.chunk_id]
    assert [item.text for item in result] == [second.raw_text, first.raw_text]
    storage.get_chunks.assert_awaited_once_with([second.chunk_id, first.chunk_id])


async def test_chunk_reads_omit_invisible_stale_and_missing(
    reading_setup,
    storage,
    read_acls,
    make_read_document,
    make_read_chunk,
    publish_read_document,
):
    document, usecase, snapshot = (
        reading_setup.document,
        reading_setup.usecase,
        reading_setup.snapshot,
    )
    current = make_read_chunk(document)
    stale = replace(current, chunk_id="stale-chunk", content_revision="old-revision")
    await storage.upsert_chunks([current, stale])
    denied_document = make_read_document("denied")
    await publish_read_document(denied_document)
    read_acls["denied"] = ResourceAcl("denied", 1, "other")
    hidden = make_read_chunk(denied_document)
    await storage.upsert_chunks([hidden])
    result = await usecase.read_chunks(
        [
            stale.chunk_id,
            hidden.chunk_id,
            "missing",
            current.chunk_id,
            current.chunk_id,
        ],
        snapshot=snapshot,
    )
    assert [item.chunk_id for item in result] == [current.chunk_id]
    assert [item.text for item in result] == [current.raw_text]


@pytest.mark.parametrize("text", ["", "# 只有标题\n"])
async def test_visible_document_without_sections_has_empty_outline(
    text, make_read_document, publish_read_document, read_acls, snapshot_reader
):
    document = await publish_read_document(make_read_document(text=text))
    read_acls[document.resource_id] = ResourceAcl(document.resource_id, 1, "reader")
    result = await DocumentReading(reader=snapshot_reader).global_outline(
        document.resource_id, snapshot=ResourceSnapshot(PermissionScope("reader"))
    )
    assert result is not None and result.outline == ""


@pytest.mark.parametrize(
    "state", ["missing", "staged", "destroyed", "missing-acl", "denied"]
)
async def test_partial_visibility_never_formats_or_reads_hidden_document(
    reading_setup,
    state,
    make_read_document,
    publish_read_document,
    publication_store,
    storage,
    read_acls,
):
    hidden = make_read_document("hidden", text="# 隐藏章节\n\n隐藏正文。\n")
    await storage.upsert_documents([hidden])
    revision = ContentRevision.create(
        resource_id=hidden.resource_id,
        document_version=hidden.document_version,
        text=hidden.text,
    )
    if state == "staged":
        await publication_store.stage_revision(revision)
    elif state != "missing":
        await publish_read_document(hidden)
    if state == "destroyed":
        await publication_store.destroy_resources([hidden.resource_id])
    if state != "missing-acl":
        read_acls[hidden.resource_id] = ResourceAcl(
            hidden.resource_id, 1, "other" if state == "denied" else "reader"
        )
    visible = reading_setup.document.structure.sections[1].section_id
    hidden_id = hidden.structure.sections[0].section_id
    ids = [hidden_id, visible, "missing", visible]
    with patch(
        "rag.application.reading.OutlineFormatter", wraps=OutlineFormatter
    ) as formatter:
        assert (
            await reading_setup.usecase.global_outline(
                hidden.resource_id, snapshot=reading_setup.snapshot
            )
            is None
        )
        formatter.assert_not_called()
        neighbors = await reading_setup.usecase.neighborhood(
            ids, snapshot=reading_setup.snapshot
        )
        assert len(neighbors) == 1 and neighbors[0].section_id == visible
        assert formatter.call_count == 1
    texts = await reading_setup.usecase.read_sections(
        ids, snapshot=reading_setup.snapshot
    )
    assert [item.section_id for item in texts] == [visible]


async def test_identical_structures_in_two_resources_do_not_share_section_identity(
    reading_setup, make_read_document, publish_read_document, read_acls
):
    other = await publish_read_document(make_read_document("other", text=SOURCE))
    read_acls["other"] = ResourceAcl("other", 1, "reader")
    own_id = reading_setup.document.structure.sections[1].section_id
    other_id = other.structure.sections[1].section_id
    assert own_id != other_id
    result = await reading_setup.usecase.read_sections(
        [other_id, own_id, other_id], snapshot=reading_setup.snapshot
    )
    assert [item.resource_id for item in result] == ["other", "resource"]


@pytest.mark.parametrize("change", ["version", "acl", "destroyed"])
async def test_first_snapshot_decision_survives_change_and_old_ids_never_read_new_version(
    reading_setup,
    change,
    make_read_document,
    publish_read_document,
    publication_store,
    read_acl_repository,
    read_acls,
):
    usecase, document, snapshot = (
        reading_setup.usecase,
        reading_setup.document,
        reading_setup.snapshot,
    )
    section_id = document.structure.sections[1].section_id
    before = await usecase.global_outline(document.resource_id, snapshot=snapshot)
    if change == "version":
        newer = await publish_read_document(make_read_document(version=2, text=SOURCE))
        assert newer.structure.sections[1].section_id != section_id
    elif change == "acl":
        read_acls[document.resource_id] = ResourceAcl(document.resource_id, 2, "other")
    else:
        await publication_store.destroy_resources([document.resource_id])
    assert await usecase.read_sections([section_id], snapshot=snapshot)
    assert (await usecase.neighborhood([section_id], snapshot=snapshot))[
        0
    ].content_revision == before.content_revision
    assert publication_store.get_states.await_count == 1
    assert read_acl_repository.get_resource_acls.await_count == 1
    assert (
        await usecase.read_sections(
            [section_id], snapshot=ResourceSnapshot(PermissionScope("reader"))
        )
        == []
    )
    if change == "version":
        assert await usecase.read_sections(
            [newer.structure.sections[1].section_id],
            snapshot=ResourceSnapshot(PermissionScope("reader")),
        )


async def test_denied_snapshot_remains_denied_after_acl_grant(reading_setup, read_acls):
    document, usecase, snapshot = (
        reading_setup.document,
        reading_setup.usecase,
        reading_setup.snapshot,
    )
    section_id = document.structure.sections[1].section_id
    read_acls[document.resource_id] = ResourceAcl(document.resource_id, 1, "other")
    assert await usecase.read_sections([section_id], snapshot=snapshot) == []
    read_acls[document.resource_id] = ResourceAcl(document.resource_id, 2, "reader")
    assert await usecase.read_sections([section_id], snapshot=snapshot) == []
    assert await usecase.read_sections(
        [section_id], snapshot=ResourceSnapshot(PermissionScope("reader"))
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("text", "错" * len(SOURCE)),
        ("document_version", 99),
        ("content_revision", "wrong"),
    ],
)
async def test_wrong_document_identity_is_omitted_without_fallback(
    reading_setup, storage, field, value
):
    document = reading_setup.document
    await storage.delete_revisions([(document.resource_id, document.content_revision)])
    await storage.upsert_documents([replace(document, **{field: value})])
    assert (
        await reading_setup.usecase.read_sections(
            [document.structure.sections[1].section_id], snapshot=reading_setup.snapshot
        )
        == []
    )


@pytest.mark.parametrize("scope", list(SectionReadScope))
@pytest.mark.parametrize(
    "start,end", [(0, len(SOURCE) + 1), (len(SOURCE) + 2, len(SOURCE) + 2)]
)
async def test_invalid_stored_range_is_a_fact_error(
    reading_setup, storage, scope, start, end
):
    document = reading_setup.document
    section = document.structure.sections[1]
    span_name = "own_span" if scope is SectionReadScope.OWN else "subtree_span"
    document.structure.sections[1] = replace(
        section, **{span_name: SourceSpan(start, end)}
    )
    await storage.upsert_documents([document])
    with pytest.raises(RuntimeError, match="span"):
        await reading_setup.usecase.read_sections(
            [section.section_id], snapshot=reading_setup.snapshot, scope=scope
        )


@pytest.mark.parametrize("scope", list(SectionReadScope))
@pytest.mark.parametrize("start,end", [(-1, 0), (5, 4)])
async def test_malformed_span_decode_exception_propagates(
    reading_setup, collections, scope, start, end
):
    # SourceSpan 自身拒绝负数与倒序；真实存储损坏在解码边界失败，不能伪造空正文。
    span_name = "own_span" if scope is SectionReadScope.OWN else "subtree_span"
    collections[0].records[0]["structure"]["sections"][1][span_name] = {
        "start_offset": start,
        "end_offset": end,
    }
    with pytest.raises(ValidationError):
        await reading_setup.usecase.read_sections(
            [reading_setup.document.structure.sections[1].section_id],
            snapshot=reading_setup.snapshot,
            scope=scope,
        )


async def test_zero_length_range_returns_empty_text(reading_setup, storage):
    document = reading_setup.document
    section = document.structure.sections[1]
    document.structure.sections[1] = replace(
        section,
        own_span=SourceSpan(
            section.own_span.start_offset, section.own_span.start_offset
        ),
    )
    await storage.upsert_documents([document])
    result = await reading_setup.usecase.read_sections(
        [section.section_id], snapshot=reading_setup.snapshot
    )
    assert len(result) == 1 and result[0].text == ""


async def test_batch_io_deduplicates_document_and_reuses_shared_snapshot(
    reading_setup, storage, publication_store, read_acl_repository, snapshot_reader
):
    storage.get_documents_by_section_ids = AsyncMock(
        wraps=storage.get_documents_by_section_ids
    )
    ids = [section.section_id for section in reading_setup.document.structure.sections]
    documents = await snapshot_reader.read_documents_by_section_ids(
        [*ids, ids[0]], snapshot=reading_setup.snapshot
    )
    assert len(documents) == 1
    storage.get_documents_by_section_ids.assert_awaited_once_with(ids)
    await reading_setup.usecase.global_outline(
        "resource", snapshot=reading_setup.snapshot
    )
    assert publication_store.get_states.await_count == 1
    assert read_acl_repository.get_resource_acls.await_count == 1


async def test_missing_fact_after_snapshot_selection_is_not_replaced(
    reading_setup, storage
):
    document = reading_setup.document
    await reading_setup.usecase.global_outline(
        document.resource_id, snapshot=reading_setup.snapshot
    )
    await storage.delete_revisions([(document.resource_id, document.content_revision)])
    assert (
        await reading_setup.usecase.read_sections(
            [document.structure.sections[1].section_id], snapshot=reading_setup.snapshot
        )
        == []
    )


async def test_shared_usecase_serves_independent_concurrent_snapshots(reading_setup):
    ids = [reading_setup.document.structure.sections[1].section_id]
    visible, hidden = await asyncio.gather(
        reading_setup.usecase.read_sections(ids, snapshot=reading_setup.snapshot),
        reading_setup.usecase.read_sections(
            ids, snapshot=ResourceSnapshot(PermissionScope("other"))
        ),
    )
    assert len(visible) == 1 and hidden == []


async def test_empty_input_has_no_io(
    snapshot_reader, collections, publication_store, read_acl_repository
):
    usecase = DocumentReading(reader=snapshot_reader)
    snapshot = ResourceSnapshot(PermissionScope("reader"))
    assert await usecase.read_sections([], snapshot=snapshot) == []
    assert await usecase.neighborhood([], snapshot=snapshot) == []
    assert await usecase.read_chunks([], snapshot=snapshot) == []
    for collection in collections:
        collection.find.assert_not_called()
    publication_store.get_states.assert_not_called()
    read_acl_repository.get_resource_acls.assert_not_called()


@pytest.mark.parametrize(
    "method,kwargs",
    [
        ("global_outline", {"max_level": -1}),
        ("neighborhood", {"sibling_steps": -1}),
        ("read_sections", {"scope": "recursive"}),
    ],
)
async def test_invalid_parameters_fail_before_io(
    snapshot_reader, collections, method, kwargs
):
    usecase = DocumentReading(reader=snapshot_reader)
    with pytest.raises(ValueError):
        await getattr(usecase, method)(
            "resource" if method == "global_outline" else ["section"],
            snapshot=ResourceSnapshot(PermissionScope("reader")),
            **kwargs,
        )
    collections[0].find.assert_not_called()


@pytest.mark.parametrize("backend", ["content", "publication", "acl"])
async def test_backend_failure_propagates_without_invisible_cache(
    reading_setup, collections, publication_store, read_acl_repository, backend
):
    error = OSError("backend unavailable")
    if backend == "content":
        collections[0].find.side_effect = error
    elif backend == "publication":
        publication_store.get_states.side_effect = error
    else:
        read_acl_repository.get_resource_acls.side_effect = error
    with pytest.raises(OSError) as caught:
        await reading_setup.usecase.read_sections(
            [reading_setup.document.structure.sections[1].section_id],
            snapshot=reading_setup.snapshot,
        )
    assert caught.value is error
    assert reading_setup.snapshot._revisions == {}
