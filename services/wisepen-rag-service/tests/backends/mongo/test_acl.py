"""本地完整 ACL 投影的公开读取行为。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rag.backends.mongo.acl import MongoResourceAclRepository


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


@pytest.fixture
def repository(record):
    cursor = SimpleNamespace(to_list=AsyncMock(return_value=[record]))
    collection = SimpleNamespace(find=Mock(return_value=cursor))
    return MongoResourceAclRepository(collection), collection, cursor


async def test_get_resource_acls_restores_complete_projection(repository):
    acl_repository, collection, cursor = repository
    acl = (await acl_repository.get_resource_acls(["resource", "resource"]))["resource"]
    assert acl.resource_id == "resource"
    assert acl.acl_revision == 1
    assert acl.owner_id == "owner"
    assert acl.readable_users == []
    assert acl.excluded_read_users == []
    assert acl.group_acls[0].default_readable is False
    assert acl.group_acls[0].readable_users == ["reader"]
    assert acl.group_acls[0].market_offer_version is None
    collection.find.assert_called_once_with({"resource_id": {"$in": ["resource"]}})
    cursor.to_list.assert_awaited_once_with(length=None)
