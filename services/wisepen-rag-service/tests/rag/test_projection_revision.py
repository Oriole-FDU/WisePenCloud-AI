from __future__ import annotations

from dataclasses import replace

from rag.application.rag.ingestion import (
    RagDocumentContent,
    RagSectionProjector,
    RagContentProjection,
    RagProjectionCheckpoint,
    RagProjectionStageAction,
    prepare_projection_stage,
)


def _projection(
    *,
    version: int = 2,
    markdown: str = "# 标题\n\n正文。",
) -> RagContentProjection:
    return RagSectionProjector().project(
        RagDocumentContent(
            resource_id="resource-1",
            document_version=version,
            markdown=markdown,
        )
    )


def test_new_projection_is_staged_with_deterministic_revision() -> None:
    projection = _projection()

    first = prepare_projection_stage(projection, None)
    repeated = prepare_projection_stage(projection, None)

    assert first.action is RagProjectionStageAction.STAGED
    assert first.content_revision == repeated.content_revision
    assert first.content_revision.startswith("rrev_")


def test_applied_revision_is_idempotent() -> None:
    projection = _projection()
    stage = prepare_projection_stage(projection, None)
    checkpoint = RagProjectionCheckpoint(
        resource_id=projection.resource_id,
        applied_content_revision=stage.content_revision,
        applied_document_version=projection.document_version,
    )

    repeated = prepare_projection_stage(projection, checkpoint)

    assert repeated.action is RagProjectionStageAction.ALREADY_APPLIED


def test_older_projection_cannot_replace_staged_or_applied_version() -> None:
    projection = _projection(version=2)
    checkpoint = RagProjectionCheckpoint(
        resource_id=projection.resource_id,
        staged_content_revision="rrev_newer",
        staged_document_version=3,
        applied_content_revision="rrev_current",
        applied_document_version=3,
    )

    stage = prepare_projection_stage(projection, checkpoint)

    assert stage.action is RagProjectionStageAction.STALE


def test_same_version_with_corrected_content_creates_new_revision() -> None:
    original = _projection()
    original_stage = prepare_projection_stage(original, None)
    corrected = _projection(markdown="# 标题\n\n修正正文。")
    checkpoint = RagProjectionCheckpoint(
        resource_id=original.resource_id,
        applied_content_revision=original_stage.content_revision,
        applied_document_version=original.document_version,
    )

    corrected_stage = prepare_projection_stage(corrected, checkpoint)

    assert corrected_stage.action is RagProjectionStageAction.STAGED
    assert corrected_stage.content_revision != original_stage.content_revision


def test_same_version_correction_is_not_blocked_by_staged_revision() -> None:
    original = _projection()
    original_stage = prepare_projection_stage(original, None)
    corrected = _projection(markdown="# 标题\n\n修正正文。")
    checkpoint = RagProjectionCheckpoint(
        resource_id=original.resource_id,
        staged_content_revision=original_stage.content_revision,
        staged_document_version=original.document_version,
    )

    corrected_stage = prepare_projection_stage(corrected, checkpoint)

    assert corrected_stage.action is RagProjectionStageAction.STAGED
    assert corrected_stage.content_revision != original_stage.content_revision


def test_staged_retry_remains_writable_until_applied() -> None:
    projection = _projection()
    stage = prepare_projection_stage(projection, None)
    checkpoint = RagProjectionCheckpoint(
        resource_id=projection.resource_id,
        staged_content_revision=stage.content_revision,
        staged_document_version=projection.document_version,
    )

    retry = prepare_projection_stage(projection, checkpoint)

    assert retry == stage


def test_revision_changes_when_resource_changes() -> None:
    projection = _projection()
    other_resource = replace(projection, resource_id="resource-2")

    first = prepare_projection_stage(projection, None)
    second = prepare_projection_stage(other_resource, None)

    assert first.content_revision != second.content_revision
