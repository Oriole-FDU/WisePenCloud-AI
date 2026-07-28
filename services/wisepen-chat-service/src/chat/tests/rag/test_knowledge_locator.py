from __future__ import annotations

import pytest

from chat.application.rag.retrieval import (
    RagPermissionScope,
    RagRetrievalRequest,
)
from chat.application.rag.retrieval.locator import RagKnowledgeLocator


class _Retriever:
    async def retrieve(self, request):
        return ("ranked",)


class _Materializer:
    async def materialize(self, *, hits, scope):
        assert hits == ("ranked",)
        assert scope.user_id == "user-1"
        return ("materialized",)


class _SectionNavigator:
    async def build_hits(self, hits):
        assert hits == ("materialized",)
        return ("located",)


@pytest.mark.asyncio
async def test_locator_runs_retrieve_materialize_and_section_promotion() -> None:
    locator = RagKnowledgeLocator(
        retriever=_Retriever(),
        materializer=_Materializer(),
        section_navigator=_SectionNavigator(),
    )

    result = await locator.locate(
        RagRetrievalRequest(
            query="查询",
            permission_scope=RagPermissionScope(user_id="user-1", group_role_map={}),
        )
    )

    assert result == ("located",)
