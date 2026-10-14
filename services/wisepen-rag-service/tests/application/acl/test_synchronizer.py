from dataclasses import asdict
from datetime import UTC, datetime
from itertools import product

import pytest
from common.core.domain import GroupRoleType

from rag.application.acl.models import PermissionScope, ResourceAcl
from rag.application.acl.synchronizer import (
    AclSynchronizer,
    AuthoritativeAclError,
    _project,
)
from rag.backends.mongo.acl import _to_domain


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
        readable_users=["reader"],
        excluded_read_users=["blocked"],
        group_acls=[],
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


@pytest.mark.parametrize(
    "fields",
    [
        {},
        {"specifiedUsersGrantedActionsMask": None, "computedGroupAcls": None},
        {"specifiedUsersGrantedActionsMask": {}, "computedGroupAcls": {}},
    ],
)
async def test_upstream_absent_null_and_empty_top_level_acl_are_legal(fields):
    resource_id = "507f1f77bcf86cd799439011"
    repository = _Repository()
    source = {
        "_id": resource_id,
        "ownerId": "owner",
        "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
        **fields,
    }
    synchronizer = AclSynchronizer(
        collection=_Collection([source]), repository=repository
    )
    assert await synchronizer.synchronize([resource_id.upper()]) == [resource_id]
    assert repository.saved[0].readable_users == []
    assert repository.saved[0].group_acls == []


@pytest.mark.parametrize(
    "fields",
    [
        {"specifiedUsersGrantedActionsMask": []},
        {"specifiedUsersGrantedActionsMask": {"blocked": True}},
        {"specifiedUsersGrantedActionsMask": {"blocked": "0"}},
        {"specifiedUsersGrantedActionsMask": {"": 0}},
        {"computedGroupAcls": []},
        {"computedGroupAcls": {"g": None}},
        {"computedGroupAcls": {"g": {"baseMask": True, "userMasks": {}}}},
        {"computedGroupAcls": {"g": {"userMasks": {}}}},
        {"computedGroupAcls": {"g": {"baseMask": 2}}},
        {"computedGroupAcls": {"g": {"baseMask": 0, "userMasks": {"user": None}}}},
    ],
)
async def test_malformed_authoritative_acl_is_not_saved_or_deleted(fields):
    resource_id = "507f1f77bcf86cd799439011"
    repository = _Repository()
    source = {
        "_id": resource_id,
        "ownerId": "owner",
        "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
        **fields,
    }
    synchronizer = AclSynchronizer(
        collection=_Collection([source]), repository=repository
    )
    with pytest.raises(AuthoritativeAclError, match=resource_id):
        await synchronizer.synchronize([resource_id])
    assert repository.saved == [] and repository.deleted == []


def test_group_projection_preserves_upstream_user_override_and_role_priority():
    # 参照 Java userMasks.getOrDefault(user, baseMask)；MARKET_VIEW 不等于 VIEW。
    for base_mask, user_mask, role in product(
        [0, 1, 2, 512],
        [None, 0, 1, 2, 512],
        [GroupRoleType.MEMBER, GroupRoleType.ADMIN, GroupRoleType.OWNER],
    ):
        source = {
            "ownerId": "owner",
            "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
            "computedGroupAcls": {
                "group": {
                    "baseMask": base_mask,
                    "userMasks": {} if user_mask is None else {"reader": user_mask},
                }
            },
        }
        acl = _to_domain(asdict(_project(source, "resource")))
        expected = role in (GroupRoleType.ADMIN, GroupRoleType.OWNER) or (
            (base_mask if user_mask is None else user_mask) & 2 != 0
        )
        assert acl.can_read(PermissionScope("reader", {"group": role})) is expected


def test_resource_user_override_precedes_group_admin_but_owner_still_wins():
    source = {
        "ownerId": "owner",
        "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
        "specifiedUsersGrantedActionsMask": {"owner": 0, "blocked": 0, "reader": 2},
        "computedGroupAcls": {"group": {"baseMask": 2, "userMasks": {}}},
    }
    acl = _to_domain(asdict(_project(source, "resource")))
    assert acl.can_read(PermissionScope("owner"))
    assert acl.can_read(PermissionScope("reader"))
    assert not acl.can_read(PermissionScope("blocked", {"group": GroupRoleType.ADMIN}))


@pytest.mark.parametrize("version", [None, 1, 2, 3])
def test_market_projection_requires_offer_version_for_members_only(version):
    source = {
        "ownerId": "owner",
        "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
        "groupBinds": [{"groupId": "market", "marketSaleInfo": {"offerVersion": 2}}],
        "computedGroupAcls": {"market": {"baseMask": 2, "userMasks": {"blocked": 0}}},
    }
    acl = _to_domain(asdict(_project(source, "resource")))
    assert acl.can_read(
        PermissionScope("reader", {"market": GroupRoleType.MEMBER}),
        document_version=version,
    ) is (version == 2)
    assert not acl.can_read(
        PermissionScope("blocked", {"market": GroupRoleType.MEMBER}),
        document_version=version,
    )
    assert acl.can_read(
        PermissionScope("blocked", {"market": GroupRoleType.ADMIN}),
        document_version=version,
    )


def test_another_group_can_grant_view_when_one_group_excludes_user():
    source = {
        "ownerId": "owner",
        "updateTime": datetime(2026, 1, 1, tzinfo=UTC),
        "computedGroupAcls": {
            "first": {"baseMask": 2, "userMasks": {"reader": 0}},
            "second": {"baseMask": 0, "userMasks": {"reader": 2}},
        },
    }
    acl = _project(source, "resource")
    assert acl.can_read(
        PermissionScope(
            "reader", {"first": GroupRoleType.MEMBER, "second": GroupRoleType.MEMBER}
        )
    )
