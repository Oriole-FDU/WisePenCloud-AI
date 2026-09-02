"""ACL 真值表和同步语义。"""

import pytest
from common.core.domain import GroupRoleType

from rag.application.publication import AclSynchronizer
from rag.domain.acl import GroupResourceAcl, PermissionScope, ResourceAcl

from .conftest import MemoryAcls


def acl(**kwargs) -> ResourceAcl:
    acl_revision = kwargs.pop("acl_revision", 1)
    return ResourceAcl(
        resource_id="resource-1",
        acl_revision=acl_revision,
        owner_id="owner",
        **kwargs,
    )


@pytest.mark.parametrize(
    ("scope", "resource_acl", "expected"),
    [
        (PermissionScope("owner"), acl(), True),
        (PermissionScope("user"), acl(readable_users=("user",)), True),
        (PermissionScope("user"), acl(excluded_read_users=("user",)), False),
        (
            PermissionScope("admin", {"group": GroupRoleType.ADMIN}),
            acl(group_acls=(GroupResourceAcl("group", False),)),
            True,
        ),
        (
            PermissionScope("member", {"group": GroupRoleType.MEMBER}),
            acl(group_acls=(GroupResourceAcl("group", True),)),
            True,
        ),
        (
            PermissionScope("member", {"group": GroupRoleType.MEMBER}),
            acl(group_acls=(GroupResourceAcl("group", True, excluded_read_users=("member",)),)),
            False,
        ),
    ],
)
def test_resource_acl_truth_table(scope, resource_acl, expected) -> None:
    assert resource_acl.can_read(scope) is expected


@pytest.mark.asyncio
async def test_acl_synchronizer_keeps_newer_local_acl() -> None:
    authoritative = MemoryAcls({"resource-1": acl(acl_revision=2)})
    local = MemoryAcls({"resource-1": acl(acl_revision=3)})

    synchronized = await AclSynchronizer(
        authoritative_reader=authoritative,
        local_repository=local,
    ).synchronize(["resource-1"])

    assert synchronized == []
    assert local.values["resource-1"].acl_revision == 3
