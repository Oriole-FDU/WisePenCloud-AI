from common.core.domain import GroupRoleType

from rag.core.persistence.neo4j.knowledge_graph_repository import _acl_predicate
from rag.core.persistence.qdrant.candidate_searcher import _permission_filter
from rag.domain.models.acl import GroupResourceAcl, PermissionScope, ResourceAcl


def test_acl_identity_contract_is_shared_by_domain_qdrant_and_neo4j() -> None:
    scope = PermissionScope(
        user_id="user-1",
        group_roles={
            "managed-group": GroupRoleType.ADMIN,
            "joined-group": GroupRoleType.MEMBER,
        },
    )
    acl = ResourceAcl(
        resource_id="resource-1",
        acl_revision=1,
        owner_id="owner-1",
        group_acls=[GroupResourceAcl(group_id="joined-group", default_readable=True)],
    )

    assert acl.can_read(scope)

    qdrant_filter = _permission_filter(scope).model_dump(exclude_none=True)
    serialized_filter = str(qdrant_filter)
    assert "user-1" in serialized_filter
    assert "managed-group" in serialized_filter
    assert "joined-group" in serialized_filter
    assert "excluded_read_users" in serialized_filter

    predicate, parameters = _acl_predicate(scope, resource_alias="resource")
    assert "resource.owner_id = $acl_user_id" in predicate
    assert "joined.group_id IN $acl_joined_group_ids" in predicate
    assert "NOT $acl_user_id IN coalesce(resource.excluded_read_users, [])" in predicate
    assert parameters["acl_user_id"] == "user-1"
    assert set(parameters["acl_managed_group_ids"]) == {"managed-group"}
    assert set(parameters["acl_joined_group_ids"]) == {
        "managed-group",
        "joined-group",
    }
