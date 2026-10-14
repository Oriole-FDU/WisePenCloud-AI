"""本地完整 ACL 投影的还原；信任已完成的权威解析与 writer 合同。"""

import pytest

from rag.backends.mongo.acl import _to_domain


@pytest.fixture
def record():
    return {
        "resource_id": "resource",
        "acl_revision": 1,
        "owner_id": "owner",
        "readable_users": [],
        "excluded_read_users": [],
        "group_acls": [
            {
                "group_id": "group",
                "default_readable": False,
                "readable_users": ["reader"],
                "excluded_read_users": [],
                "market_offer_version": None,
            }
        ],
    }


def test_complete_acl_is_parsed_without_coercion(record):
    acl = _to_domain(record)
    assert acl.group_acls[0].default_readable is False
    assert acl.group_acls[0].readable_users == ["reader"]


@pytest.mark.parametrize(
    "field", ["readable_users", "excluded_read_users", "group_acls"]
)
def test_missing_local_writer_fields_are_not_defaulted(record, field):
    del record[field]
    with pytest.raises(KeyError):
        _to_domain(record)


def test_missing_group_writer_field_is_not_defaulted(record):
    del record["group_acls"][0]["excluded_read_users"]
    with pytest.raises(KeyError):
        _to_domain(record)
