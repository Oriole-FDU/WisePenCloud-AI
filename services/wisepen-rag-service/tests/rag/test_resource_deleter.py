import pytest

from rag.application.rag.index import ResourceDeleter


class _Failure:
    def __init__(self, step=None) -> None:
        self.step = step
        self.failed = False
        self.calls = []

    def hit(self, step):
        self.calls.append(step)
        if self.step == step and not self.failed:
            self.failed = True
            raise RuntimeError(step)


class _ResourceWriter:
    def __init__(self, failure) -> None:
        self._failure = failure

    async def clear_resource_states(self, resource_ids):
        self._failure.hit("state")

    async def delete_resources(self, resource_ids):
        self._failure.hit("content")


class _DeleteTarget:
    def __init__(self, failure, step) -> None:
        self._failure = failure
        self._step = step

    async def delete_resources(self, resource_ids):
        self._failure.hit(self._step)


def _deleter(failure):
    return ResourceDeleter(
        resource_writer=_ResourceWriter(failure),
        retrieval_writer=_DeleteTarget(failure, "qdrant"),
        graph_repository=_DeleteTarget(failure, "neo4j"),
        generation_artifacts=_DeleteTarget(failure, "artifacts"),
        acl_store=_DeleteTarget(failure, "acl"),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failed_step",
    ["state", "qdrant", "neo4j", "content", "artifacts", "acl"],
)
async def test_delete_failure_is_retried_without_restoring_resource_state(
    failed_step,
) -> None:
    failure = _Failure(failed_step)
    deleter = _deleter(failure)

    expected_error = RuntimeError if failed_step == "state" else ExceptionGroup
    with pytest.raises(expected_error):
        await deleter.delete_resources(["resource-1"])

    await deleter.delete_resources(["resource-1"])

    assert failure.calls.count("state") == 2
    for step in ("qdrant", "neo4j", "content", "artifacts", "acl"):
        assert step in failure.calls
    assert failure.calls[0] == "state"


@pytest.mark.asyncio
async def test_repeated_and_empty_deletion_are_idempotent() -> None:
    failure = _Failure()
    deleter = _deleter(failure)

    await deleter.delete_resources(["resource-1", "resource-1"])
    await deleter.delete_resources(["resource-1"])
    await deleter.delete_resources([])

    assert failure.calls.count("state") == 2
    assert len(failure.calls) == 12
