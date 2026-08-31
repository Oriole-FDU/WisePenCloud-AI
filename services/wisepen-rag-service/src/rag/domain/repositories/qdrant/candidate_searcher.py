"""候选召回能力的领域 port。"""

from collections.abc import Sequence
from typing import Protocol

from rag.domain.models.acl import PermissionScope
from rag.domain.models.retrieval import RetrievalCandidate


class CandidateSearcher(Protocol):
    """按查询事实返回尚未排序和核验的检索候选。"""

    async def search(
        self,
        *,
        lexical_query: str,
        semantic_vector: Sequence[float],
        permission_scope: PermissionScope,
        limit: int,
    ) -> list[RetrievalCandidate]: ...
