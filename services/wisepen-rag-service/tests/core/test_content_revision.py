from hashlib import sha256

from rag.core.schema import ContentRevision


def test_revision_uses_the_exact_text_digest_and_scopes_identity():
    text = "中文正文\n"
    revision = ContentRevision.create(
        resource_id="resource", document_version=3, text=text
    )
    digest = sha256(text.encode("utf-8")).hexdigest()

    assert revision.content_sha256 == digest
    assert revision.content_revision == f"resource@3#{digest}"
    assert (
        ContentRevision.create(resource_id="resource", document_version=3, text=text)
        == revision
    )
    assert (
        ContentRevision.create(resource_id="other", document_version=3, text=text)
        != revision
    )
    assert (
        ContentRevision.create(resource_id="resource", document_version=4, text=text)
        != revision
    )
    assert (
        ContentRevision.create(
            resource_id="resource", document_version=3, text=f"{text} "
        )
        != revision
    )
