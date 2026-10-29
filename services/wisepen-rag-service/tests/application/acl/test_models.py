from common.core.domain import GroupRoleType

from rag.domain.models.acl import GroupResourceAcl, PermissionScope, ResourceAcl


def test_resource_acl_uses_existing_priority_order():
    acl = ResourceAcl(
        resource_id="resource",
        acl_revision=1,
        owner_id="owner",
        readable_users=("reader",),
        excluded_read_users=("blocked",),
        group_acls=(
            GroupResourceAcl(
                group_id="group",
                default_readable=True,
                excluded_read_users=("group-blocked",),
            ),
        ),
    )

    assert acl.can_read(PermissionScope("owner"))
    assert acl.can_read(PermissionScope("reader"))
    assert not acl.can_read(PermissionScope("blocked"))
    assert acl.can_read(
        PermissionScope.from_group_roles("member", {"group": GroupRoleType.MEMBER})
    )
    assert not acl.can_read(
        PermissionScope.from_group_roles("group-blocked", {"group": GroupRoleType.MEMBER})
    )
    assert not acl.can_read(PermissionScope("stranger"))


def test_group_manager_can_read_private_group():
    acl = ResourceAcl(
        resource_id="resource",
        acl_revision=1,
        owner_id="owner",
        group_acls=(GroupResourceAcl(group_id="group", default_readable=False),),
    )
    scope = PermissionScope.from_group_roles("manager", {"group": GroupRoleType.ADMIN})
    assert acl.can_read(scope)
