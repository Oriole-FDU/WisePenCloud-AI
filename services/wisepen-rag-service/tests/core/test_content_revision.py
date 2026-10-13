"""内容身份保留完整原文，框架模型不依赖发布或执行阶段。"""

from dataclasses import FrozenInstanceError, fields
from hashlib import sha256

import pytest

from rag.core.schema import ContentRevision


@pytest.mark.parametrize("text", ["", "中文😀\n", "  正文\n\n", "é", "e\u0301"])
def test_revision_uses_exact_utf8_text_and_full_digest(text):
    revision = ContentRevision.create(
        resource_id="resource", document_version=3, text=text
    )
    digest = sha256(text.encode("utf-8")).hexdigest()
    assert revision.content_sha256 == digest
    assert revision.content_revision == f"resource@3#{digest}"
    assert len(digest) == 64
    assert (
        ContentRevision.create(resource_id="resource", document_version=3, text=text)
        == revision
    )


def test_revision_is_frozen_slotted_and_identity_includes_resource_and_version():
    revision = ContentRevision.create(
        resource_id="resource", document_version=3, text="正文"
    )
    assert [field.name for field in fields(revision)] == [
        "resource_id",
        "document_version",
        "content_sha256",
    ]
    assert not hasattr(revision, "__dict__")
    assert revision.__class__.__module__ == "rag.core.schema.content"
    with pytest.raises(FrozenInstanceError):
        revision.document_version = 4
    assert (
        ContentRevision.create(
            resource_id="other", document_version=3, text="正文"
        ).content_revision
        != revision.content_revision
    )
    assert (
        ContentRevision.create(
            resource_id="resource", document_version=4, text="正文"
        ).content_revision
        != revision.content_revision
    )
    assert (
        ContentRevision.create(
            resource_id="resource", document_version=3, text="正文\n"
        )
        != revision
    )
    assert ContentRevision.create(
        resource_id="resource", document_version=3, text="é"
    ) != ContentRevision.create(
        resource_id="resource", document_version=3, text="e\u0301"
    )
