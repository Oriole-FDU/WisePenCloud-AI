from collections.abc import Mapping, Sequence

import pytest
from common.core.domain import GroupRoleType

from rag.application.rag.acl import PermissionAuthorizer
from rag.domain.models.acl import (
    GroupResourceAcl,
    PermissionScope,
    ResourceAcl,
)
from rag.domain.repositories.mongo.resource_acl_store import ResourceAclStore


def _resource_acl(
    *,
    readable_users: list[str] | None = None,
    excluded_read_users: list[str] | None = None,
    group_acls: list[GroupResourceAcl] | None = None,
) -> ResourceAcl:
    return ResourceAcl(
        resource_id="resource-1",
        acl_revision=1,
        owner_id="owner-1",
        readable_users=readable_users or [],
        excluded_read_users=excluded_read_users or [],
        group_acls=group_acls or [],
    )


@pytest.mark.parametrize(
    ("scope", "acl", "expected"),
    [
        (
            PermissionScope(user_id="owner-1"),
            _resource_acl(),
            True,
        ),
        (
            PermissionScope(user_id="user-1"),
            _resource_acl(readable_users=["user-1"]),
            True,
        ),
        (
            PermissionScope(user_id="user-1"),
            _resource_acl(excluded_read_users=["user-1"]),
            False,
        ),
        (
            PermissionScope(
                user_id="manager-1",
                group_roles={"group-1": GroupRoleType.ADMIN},
            ),
            _resource_acl(
                group_acls=[
                    GroupResourceAcl(group_id="group-1", default_readable=False)
                ]
            ),
            True,
        ),
        (
            PermissionScope(
                user_id="member-1",
                group_roles={"group-1": GroupRoleType.MEMBER},
            ),
            _resource_acl(
                group_acls=[
                    GroupResourceAcl(group_id="group-1", default_readable=True)
                ]
            ),
            True,
        ),
        (
            PermissionScope(
                user_id="member-1",
                group_roles={"group-1": GroupRoleType.MEMBER},
            ),
            _resource_acl(
                group_acls=[
                    GroupResourceAcl(
                        group_id="group-1",
                        default_readable=True,
                        excluded_read_users=["member-1"],
                    )
                ]
            ),
            False,
        ),
        (
            PermissionScope(
                user_id="member-1",
                group_roles={"group-1": GroupRoleType.MEMBER},
            ),
            _resource_acl(
                group_acls=[
                    GroupResourceAcl(
                        group_id="group-1",
                        default_readable=False,
                        readable_users=["member-1"],
                    )
                ]
            ),
            True,
        ),
        (
            PermissionScope(
                user_id="outsider-1",
                group_roles={"group-1": None},
            ),
            _resource_acl(
                group_acls=[
                    GroupResourceAcl(group_id="group-1", default_readable=True)
                ]
            ),
            False,
        ),
    ],
)
def test_can_read_resource_acl_truth_table(
    scope: PermissionScope,
    acl: ResourceAcl,
    expected: bool,
) -> None:
    assert acl.can_read(scope) is expected


def test_resource_exclusion_blocks_group_grant_but_not_direct_grant() -> None:
    acl = _resource_acl(
        readable_users=["user-1"],
        excluded_read_users=["user-1"],
        group_acls=[
            GroupResourceAcl(group_id="group-1", default_readable=True),
        ],
    )

    assert acl.can_read(
        PermissionScope(
            user_id="user-1",
            group_roles={"group-1": GroupRoleType.MEMBER},
        ),
    )

    assert not _resource_acl(
        excluded_read_users=["user-1"],
        group_acls=[
            GroupResourceAcl(group_id="group-1", default_readable=True),
        ],
    ).can_read(
        PermissionScope(
            user_id="user-1",
            group_roles={"group-1": GroupRoleType.MEMBER},
        ),
    )


def test_permission_scope_separates_managed_joined_and_missing_groups() -> None:
    scope = PermissionScope.from_group_roles(
        "user-1",
        {
            "owner-group": GroupRoleType.OWNER,
            "admin-group": GroupRoleType.ADMIN,
            "member-group": GroupRoleType.MEMBER,
            "not-member-group": GroupRoleType.NOT_MEMBER,
            "missing-group": None,
        },
    )

    assert scope.managed_group_ids == {"owner-group", "admin-group"}
    assert scope.joined_group_ids == {
        "owner-group",
        "admin-group",
        "member-group",
    }


class _AclReader(ResourceAclStore):
    def __init__(self, resource_acls: Mapping[str, ResourceAcl]) -> None:
        self.resource_acls = resource_acls
        self.requested_ids: list[str] = []

    async def get_resource_acl(self, resource_id: str) -> ResourceAcl | None:
        return self.resource_acls.get(resource_id)

    async def get_resource_acls(
        self,
        resource_ids: Sequence[str],
    ) -> Mapping[str, ResourceAcl]:
        self.requested_ids = list(resource_ids)
        return {
            resource_id: self.resource_acls[resource_id]
            for resource_id in resource_ids
            if resource_id in self.resource_acls
        }

    async def save_if_newer(self, resource_acl: ResourceAcl) -> bool:
        raise NotImplementedError

    async def delete_resources(self, resource_ids: Sequence[str]) -> None:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_authorizer_preserves_order_deduplicates_and_fails_closed() -> None:
    reader = _AclReader(
        {
            "resource-1": _resource_acl(readable_users=["user-1"]),
            "resource-2": _resource_acl(),
        }
    )

    authorizer = PermissionAuthorizer(local_store=reader)
    readable = await authorizer.readable_resource_ids(
        ["resource-2", "missing", "resource-1", "resource-2"],
        scope=PermissionScope(user_id="user-1"),
    )

    assert readable == ["resource-1"]
    assert reader.requested_ids == ["resource-2", "missing", "resource-1"]


@pytest.mark.asyncio
async def test_authorizer_fails_closed_when_acl_is_missing() -> None:
    reader = _AclReader({})

    authorizer = PermissionAuthorizer(local_store=reader)
    assert not await authorizer.authorize_resource(
        resource_id="missing",
        scope=PermissionScope(user_id="user-1"),
    )


@pytest.mark.asyncio
async def test_authorizer_reads_single_acl_without_batch_lookup() -> None:
    reader = _AclReader({"resource-1": _resource_acl(readable_users=["user-1"])})

    authorizer = PermissionAuthorizer(local_store=reader)

    assert await authorizer.authorize_resource(
        resource_id="resource-1",
        scope=PermissionScope(user_id="user-1"),
    )
    assert reader.requested_ids == []
