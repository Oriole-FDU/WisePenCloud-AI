"""不透明章节 ID 的批量文档定位、去重顺序与数据库失败合同。"""

from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from rag.application.publication.publish import DocumentReadyEvent


def make_document(resource_id, version):
    return DocumentReadyEvent.model_validate(
        {
            "resourceId": resource_id,
            "version": version,
            "content": "# A\n\n正文\n\n## B\n\n子正文\n",
        }
    ).to_document()


async def test_lookup_batches_deduplicates_documents_and_restores_request_order(
        storage, collections
):
    first, newer, other = (
        make_document("a", 1),
        make_document("a", 2),
        make_document("b", 1),
    )
    await storage.upsert_documents([first, newer, other])
    ids = [
        other.structure.sections[1].section_id,
        "missing",
        first.structure.sections[1].section_id,
        other.structure.sections[0].section_id,
        newer.structure.sections[0].section_id,
        first.structure.sections[0].section_id,
        other.structure.sections[1].section_id,
    ]
    assert await storage.get_documents_by_section_ids(ids) == [other, first, newer]
    collections[0].find.assert_called_once_with(
        {"structure.sections.section_id": {"$in": list(dict.fromkeys(ids))}}
    )


async def test_lookup_missing_sections_returns_no_documents(storage, collections):
    await storage.upsert_documents([make_document("a", 1)])
    assert await storage.get_documents_by_section_ids(["missing"]) == []
    assert collections[0].find.call_count == 1


@pytest.mark.parametrize("failure", ["query", "decode"])
async def test_lookup_propagates_backend_and_decode_errors(
        storage, collections, failure
):
    document = make_document("a", 1)
    await storage.upsert_documents([document])
    if failure == "query":
        error = OSError("database unavailable")
        collections[0].find = Mock(side_effect=error)
    else:
        del collections[0].records[0]["text"]
    with pytest.raises(OSError if failure == "query" else ValidationError):
        await storage.get_documents_by_section_ids(
            [document.structure.sections[0].section_id]
        )
