from datetime import UTC, datetime

import pytest

from rag.acl.models import ResourceAcl
from rag.acl.synchronizer import AclSynchronizer, AuthoritativeAclError


class _Cursor:
    def __init__(self, records):
        self._records = records

    async def to_list(self, *, length):
        return self._records


class _Collection:
    def __init__(self, records):
        self.records = records

    def find(self, _query):
        return _Cursor(self.records)


class _Repository:
    def __init__(self):
        self.saved = []
        self.deleted = []

    async def save(self, resource_acl):
        self.saved.append(resource_acl)
        return True

    async def delete(self, resource_id):
        self.deleted.append(resource_id)


@pytest.mark.asyncio
async def test_authoritative_acl_is_projected_and_saved():
    resource_id = "507f1f77bcf86cd799439011"
    repository = _Repository()
    synchronizer = AclSynchronizer(
        collection=_Collection(
            [
                {
                    "_id": resource_id,
                    "ownerId": "owner",
                    "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
                    "specifiedUsersGrantedActionsMask": {"reader": 2, "blocked": 0},
                    "computedGroupAcls": {},
                }
            ]
        ),
        repository=repository,
    )

    assert await synchronizer.synchronize([resource_id]) == [resource_id]
    assert repository.saved[0] == ResourceAcl(
        resource_id=resource_id,
        acl_revision=1767225600000,
        owner_id="owner",
        readable_users=("reader",),
        excluded_read_users=("blocked",),
        group_acls=(),
    )


@pytest.mark.asyncio
async def test_missing_authoritative_record_deletes_local_projection():
    resource_id = "507f1f77bcf86cd799439011"
    repository = _Repository()
    synchronizer = AclSynchronizer(collection=_Collection([]), repository=repository)

    assert await synchronizer.synchronize([resource_id]) == []
    assert repository.deleted == [resource_id]


@pytest.mark.asyncio
async def test_invalid_resource_id_fails_closed():
    synchronizer = AclSynchronizer(collection=_Collection([]), repository=_Repository())

    with pytest.raises(AuthoritativeAclError):
        await synchronizer.synchronize(["not-an-object-id"])
