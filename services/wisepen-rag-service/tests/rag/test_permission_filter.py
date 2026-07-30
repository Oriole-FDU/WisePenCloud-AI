from __future__ import annotations

import pytest
from rag.application.rag.retrieval import (
    RagPermissionFilterBuilder,
    RagPermissionScope,
)
from rag.application.rag.acl import (
    RagComputedGroupAclProjection,
    RagPermissionAuthorizer,
    RagResourceAclProjection,
)
from common.core.domain import GroupRoleType


def test_permission_scope_uses_trusted_group_roles() -> None:
    scope = RagPermissionScope(
        user_id="user-1",
        group_role_map={
            "owner-group": GroupRoleType.OWNER,
            "admin-group": GroupRoleType.ADMIN,
            "member-group": GroupRoleType.MEMBER,
            "not-member": GroupRoleType.NOT_MEMBER,
            "invalid-role": None,
        },
    )

    assert scope.managed_group_ids == ("owner-group", "admin-group")
    assert scope.joined_group_ids == (
        "owner-group",
        "admin-group",
        "member-group",
    )


def test_qdrant_filter_applies_resource_override_before_group_acl() -> None:
    query = RagPermissionFilterBuilder().build_qdrant_filter(_scope())
    payload = query.model_dump(exclude_none=True)

    group_branch = payload["should"][2]
    assert group_branch["must_not"] == [
        {
            "key": "excluded_read_users",
            "match": {"value": "user-1"},
        }
    ]
    assert len(group_branch["should"]) == 3


def test_neo4j_filter_uses_acl_nodes_and_same_override_priority() -> None:
    predicate, params = RagPermissionFilterBuilder().build_neo4j_predicate(
        _scope(),
        node_alias="resource",
    )

    assert (
        "NOT $rag_acl_user_id IN coalesce(resource.excluded_read_users, [])"
        in predicate
    )
    assert "(resource)-[:HAS_GROUP_ACL]->(acl:ResourceGroupAcl)" in predicate
    assert params == {
        "rag_acl_user_id": "user-1",
        "rag_acl_managed_group_ids": ["admin-group"],
        "rag_acl_joined_group_ids": ["admin-group", "member-group"],
    }


def test_neo4j_filter_rejects_query_injection_alias() -> None:
    with pytest.raises(ValueError, match="valid identifier"):
        RagPermissionFilterBuilder().build_neo4j_predicate(
            _scope(),
            node_alias="resource) MATCH (other",
        )


@pytest.mark.parametrize(
    ("user_id", "roles", "expected"),
    (
        ("owner", {}, True),
        ("direct-reader", {}, True),
        ("resource-blocked", {"public": GroupRoleType.MEMBER}, False),
        ("managed", {"private": GroupRoleType.ADMIN}, True),
        ("public-member", {"public": GroupRoleType.MEMBER}, True),
        ("group-blocked", {"public": GroupRoleType.MEMBER}, False),
        ("private-granted", {"private": GroupRoleType.MEMBER}, True),
        ("stranger", {}, False),
    ),
)
@pytest.mark.asyncio
async def test_local_authorizer_matches_resource_view_priority(
    user_id: str,
    roles: dict[str, GroupRoleType],
    expected: bool,
) -> None:
    projection = RagResourceAclProjection(
        resource_id="resource-1",
        acl_revision=1,
        owner_id="owner",
        readable_users=("direct-reader",),
        excluded_read_users=("resource-blocked",),
        computed_group_acls=(
            RagComputedGroupAclProjection(
                group_id="public",
                is_readable=True,
                excluded_read_users=("group-blocked",),
            ),
            RagComputedGroupAclProjection(
                group_id="private",
                is_readable=False,
                readable_users=("private-granted",),
            ),
        ),
    )

    accessible = await RagPermissionAuthorizer(
        repository=_ProjectionRepository(projection)
    ).accessible_resource_ids(
        (projection.resource_id,),
        RagPermissionScope(user_id=user_id, group_role_map=roles),
    )

    assert (projection.resource_id in accessible) is expected


class _ProjectionRepository:
    def __init__(self, projection: RagResourceAclProjection) -> None:
        self.projection = projection

    async def get_projections(
        self,
        resource_ids: tuple[str, ...],
    ) -> dict[str, RagResourceAclProjection]:
        return {
            self.projection.resource_id: self.projection
            for resource_id in resource_ids
            if resource_id == self.projection.resource_id
        }


def _scope() -> RagPermissionScope:
    return RagPermissionScope(
        user_id="user-1",
        group_role_map={
            "admin-group": GroupRoleType.ADMIN,
            "member-group": GroupRoleType.MEMBER,
        },
    )
