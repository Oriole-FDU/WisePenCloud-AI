"""Mongo 发布状态的版本规则、条件更新竞争与永久销毁合同。"""

from types import SimpleNamespace

import pytest
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from rag.domain.models.publication import ContentConflictError, PublicationAction, PublicationStateChangedError, ResourcePublicationState, RevisionNotStagedError
from rag.domain.models import ContentRevision


def revision(version=1, text="正文", resource_id="resource"):
    return ContentRevision.create(
        resource_id=resource_id, document_version=version, text=text
    )


async def test_destroyed_cursor_orders_filters_and_does_not_skip_late_markers(
        publication_store,
):
    await publication_store.destroy_resources(["z", "a", "m"])
    await publication_store.stage_revision(revision(resource_id="live"))
    assert await publication_store.list_destroyed_resource_ids(limit=2) == ["a", "m"]
    assert await publication_store.list_destroyed_resource_ids(
        after_resource_id="m", limit=2
    ) == ["z"]
    assert (
            await publication_store.list_destroyed_resource_ids(after_resource_id="z") == []
    )
    await publication_store.destroy_resources(["b"])
    assert await publication_store.list_destroyed_resource_ids() == ["a", "b", "m", "z"]


async def test_stage_apply_replay_and_newer_candidate_preserve_applied(
        publication_store, publication_collection
):
    first, second = revision(), revision(2)
    assert await publication_store.stage_revision(first) is PublicationAction.STAGED
    assert await publication_store.stage_revision(first) is PublicationAction.STAGED
    assert await publication_store.get_states(["resource"]) == [
        ResourcePublicationState("resource", first)
    ]
    assert await publication_store.apply_revision(first) is PublicationAction.APPLIED
    assert (
            await publication_store.stage_revision(first)
            is PublicationAction.ALREADY_APPLIED
    )
    assert (
            await publication_store.apply_revision(first)
            is PublicationAction.ALREADY_APPLIED
    )
    assert await publication_store.stage_revision(second) is PublicationAction.STAGED
    assert await publication_store.get_states(["resource"]) == [
        ResourcePublicationState("resource", second, first)
    ]
    assert await publication_store.apply_revision(second) is PublicationAction.APPLIED
    assert await publication_store.get_states(["resource"]) == [
        ResourcePublicationState("resource", None, second)
    ]
    record = publication_collection.records[0]
    assert set(record) == {
        "_id",
        "resource_id",
        "staged_revision",
        "applied_revision",
        "destroyed",
    }
    assert record["applied_revision"] == {
        "resource_id": "resource",
        "document_version": 2,
        "content_sha256": second.content_sha256,
    }


@pytest.mark.parametrize("applied", [False, True])
async def test_same_version_conflict_does_not_change_state(publication_store, applied):
    original = revision()
    await publication_store.stage_revision(original)
    if applied:
        await publication_store.apply_revision(original)
    before = await publication_store.get_states(["resource"])
    conflicting = revision(text="另一份正文")
    with pytest.raises(ContentConflictError):
        await publication_store.stage_revision(conflicting)
    with pytest.raises(ContentConflictError):
        await publication_store.apply_revision(conflicting)
    assert await publication_store.get_states(["resource"]) == before


async def test_stale_precedes_old_version_conflict_and_cannot_apply(publication_store):
    first, newer = revision(), revision(3)
    await publication_store.stage_revision(first)
    await publication_store.apply_revision(first)
    await publication_store.stage_revision(newer)
    for old in (first, revision(text="旧版本漂移"), revision(2)):
        assert await publication_store.stage_revision(old) is PublicationAction.STALE
        assert await publication_store.apply_revision(old) is PublicationAction.STALE
    assert await publication_store.get_states(["resource"]) == [
        ResourcePublicationState("resource", newer, first)
    ]


async def test_apply_never_staged_or_unstaged_new_version_fails(publication_store):
    with pytest.raises(RevisionNotStagedError):
        await publication_store.apply_revision(revision())
    await publication_store.stage_revision(revision())
    with pytest.raises(RevisionNotStagedError):
        await publication_store.apply_revision(revision(2))
    await publication_store.apply_revision(revision())
    with pytest.raises(RevisionNotStagedError):
        await publication_store.apply_revision(revision(2))


async def test_state_batch_read_is_scoped_deduplicated_and_ordered(publication_store):
    first, other = revision(), revision(resource_id="other")
    await publication_store.stage_revision(first)
    await publication_store.stage_revision(other)
    assert await publication_store.get_states(
        ["resource", "missing", "other", "resource"]
    ) == [
               ResourcePublicationState("resource", first),
               ResourcePublicationState("other", other),
           ]


async def test_empty_batches_have_no_io(
        publication_store, publication_collection
):
    assert await publication_store.get_states([]) == []
    assert await publication_store.destroy_resources([]) is None
    publication_collection.find.assert_not_called()
    publication_collection.bulk_write.assert_not_awaited()



async def test_destroy_is_permanent_idempotent_scoped_and_can_precede_stage(
        publication_store,
):
    first, other = revision(), revision(resource_id="other")
    await publication_store.stage_revision(first)
    await publication_store.apply_revision(first)
    await publication_store.stage_revision(revision(2))
    await publication_store.stage_revision(other)
    await publication_store.destroy_resources(["resource", "unseen", "resource"])
    await publication_store.destroy_resources(["resource", "unseen"])
    assert await publication_store.get_states(["resource", "unseen", "other"]) == [
        ResourcePublicationState("resource", destroyed=True),
        ResourcePublicationState("unseen", destroyed=True),
        ResourcePublicationState("other", other),
    ]
    for candidate in (
            first,
            revision(100),
            revision(text="冲突"),
            revision(resource_id="unseen"),
    ):
        assert (
                await publication_store.stage_revision(candidate)
                is PublicationAction.DESTROYED
        )
        assert (
                await publication_store.apply_revision(candidate)
                is PublicationAction.DESTROYED
        )


@pytest.mark.parametrize("fail_second", [False, True])
async def test_destroy_batches_preserve_partial_success(
        publication_store, publication_collection, fail_second
):
    ids = [f"resource-{index}" for index in range(1001)]
    if fail_second:
        publication_collection.fail_bulk_call = 2
        with pytest.raises(RuntimeError) as caught:
            await publication_store.destroy_resources([*ids, ids[0]])
        assert caught.value is publication_collection.error
    else:
        await publication_store.destroy_resources([*ids, ids[0]])
    sizes = [
        len(call.args[0]) for call in publication_collection.bulk_write.call_args_list
    ]
    assert sizes == ([500, 500] if fail_second else [500, 500, 1])
    assert len(publication_collection.records) == (500 if fail_second else 1001)
    assert all(record["destroyed"] for record in publication_collection.records)


@pytest.mark.parametrize(
    "competing", ["newer", "applied", "destroyed", "same", "missing", "conflict"]
)
async def test_initial_insert_competition_is_classified_without_retry(
        publication_store, publication_collection, competing
):
    candidate = revision()

    async def race():
        if competing == "missing":
            # 插入竞争失败后记录又消失，不能假设自己已经 stage 成功。
            raise DuplicateKeyError("lost insertion")
        if competing == "destroyed":
            await publication_store.destroy_resources(["resource"])
        else:
            other = candidate
            if competing == "newer":
                other = revision(2)
            elif competing == "conflict":
                other = revision(text="冲突正文")
            await publication_store.stage_revision(other)
            if competing == "applied":
                await publication_store.apply_revision(candidate)

    publication_collection.before_insert = race
    if competing in ("same", "missing"):
        with pytest.raises(PublicationStateChangedError):
            await publication_store.stage_revision(candidate)
    elif competing == "conflict":
        with pytest.raises(ContentConflictError):
            await publication_store.stage_revision(candidate)
    else:
        expected = {
            "newer": PublicationAction.STALE,
            "applied": PublicationAction.ALREADY_APPLIED,
            "destroyed": PublicationAction.DESTROYED,
        }[competing]
        assert await publication_store.stage_revision(candidate) is expected
    assert publication_collection.insert_one.await_count <= 2


@pytest.mark.parametrize(
    "change", ["newer", "applied", "destroyed", "conflict", "eligible"]
)
async def test_stage_conditions_cover_both_pointers_and_destroyed(
        publication_store, publication_collection, change
):
    await publication_store.stage_revision(revision())
    candidate = revision(2)

    async def race():
        if change == "newer":
            await publication_store.stage_revision(revision(3))
        elif change == "applied":
            await publication_store.stage_revision(candidate)
            await publication_store.apply_revision(candidate)
        elif change == "destroyed":
            await publication_store.destroy_resources(["resource"])
        elif change == "conflict":
            await publication_store.stage_revision(revision(2, text="冲突正文"))
        else:
            await publication_store.apply_revision(revision())

    publication_collection.before_update = race
    if change == "conflict":
        with pytest.raises(ContentConflictError):
            await publication_store.stage_revision(candidate)
    elif change == "eligible":
        with pytest.raises(PublicationStateChangedError):
            await publication_store.stage_revision(candidate)
    else:
        expected = {
            "newer": PublicationAction.STALE,
            "applied": PublicationAction.ALREADY_APPLIED,
            "destroyed": PublicationAction.DESTROYED,
        }[change]
        assert await publication_store.stage_revision(candidate) is expected
    # 竞争中完成的状态不能被外层任务覆盖；整个方法不自动重试条件写入。
    state = (await publication_store.get_states(["resource"]))[0]
    if change == "newer":
        assert state.staged_revision == revision(3)
    if change == "eligible":
        assert state.applied_revision == revision() and state.staged_revision is None


@pytest.mark.parametrize("change", ["newer", "destroyed", "applied"])
async def test_apply_conditions_block_replaced_or_destroyed_candidate(
        publication_store, publication_collection, change
):
    candidate = revision()
    await publication_store.stage_revision(candidate)

    async def race():
        if change == "newer":
            await publication_store.stage_revision(revision(2))
        elif change == "destroyed":
            await publication_store.destroy_resources(["resource"])
        else:
            await publication_store.apply_revision(candidate)

    publication_collection.before_update = race
    expected = {
        "newer": PublicationAction.STALE,
        "destroyed": PublicationAction.DESTROYED,
        "applied": PublicationAction.ALREADY_APPLIED,
    }[change]
    assert await publication_store.apply_revision(candidate) is expected


async def test_apply_missing_stage_observation_is_not_retried(
        publication_store, publication_collection
):
    candidate = revision()
    await publication_store.stage_revision(candidate)

    async def clear():
        publication_collection.records[0]["staged_revision"] = None

    async def restage():
        await publication_store.stage_revision(candidate)

    publication_collection.before_update = clear
    publication_collection.after_find = restage
    # 分类读取之后候选又出现，也不能依据新状态偷偷执行第二次 apply。
    with pytest.raises(RevisionNotStagedError):
        await publication_store.apply_revision(candidate)
    assert (await publication_store.get_states(["resource"]))[
               0
           ].staged_revision == candidate


async def test_apply_failed_condition_with_candidate_present_is_state_changed(
        publication_store, publication_collection
):
    candidate = revision()
    await publication_store.stage_revision(candidate)
    publication_collection.update_one.return_value = SimpleNamespace(matched_count=0)
    publication_collection.update_one.side_effect = None
    with pytest.raises(PublicationStateChangedError):
        await publication_store.apply_revision(candidate)
    publication_collection.update_one.assert_awaited_once()


async def test_store_io_and_corrupt_state_failures_propagate(
        publication_store, publication_collection
):
    error = RuntimeError("database unavailable")
    publication_collection.find_one.side_effect = error
    with pytest.raises(RuntimeError) as caught:
        await publication_store.stage_revision(revision())
    assert caught.value is error
    publication_collection.records.append(
        {"resource_id": "broken", "staged_revision": {"document_version": 1}}
    )
    with pytest.raises(ValidationError):
        await publication_store.get_states(["broken"])
