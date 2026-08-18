"""VERIFY 用例：按 GraphEvidence 核验图谱原文与 ReadingBlock 归属。"""

from collections.abc import Sequence

from rag.domain.models.graph import GraphEvidence
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedGraphEvidence,
    PublishedResourceCorruptError,
    PublishedResourceReader,
    PublishedResourceRevisionError,
)

from rag.application.rag.navigate.evidence_verifiers.source_evidence import (
    EvidenceCorruptError,
    EvidenceNotFoundError,
    EvidenceRevisionError,
)


class GraphEvidenceVerifier:
    """按资源 revision 批量解析图谱证据，并保持调用方输入顺序。"""

    __slots__ = ("_reader",)

    def __init__(self, *, reader: PublishedResourceReader) -> None:
        self._reader = reader

    async def verify(
        self,
        evidence: Sequence[GraphEvidence],
    ) -> list[PublishedGraphEvidence]:
        if not evidence:
            return []

        # 按 (resource_id, revision) 分组，批量回源到权威存储
        grouped: dict[tuple[str, str], list[GraphEvidence]] = {}
        for item in evidence:
            grouped.setdefault(
                (item.resource_id, item.content_revision),
                [],
            ).append(item)

        records_by_id: dict[str, PublishedGraphEvidence] = {}
        for (resource_id, content_revision), items in grouped.items():
            try:
                records = await self._reader.get_graph_evidence(
                    resource_id,
                    content_revision,
                    items,
                )
            except PublishedResourceRevisionError as error:
                raise EvidenceRevisionError(str(error)) from error
            except PublishedResourceCorruptError as error:
                raise EvidenceCorruptError(str(error)) from error
            if records is None:
                raise EvidenceNotFoundError(resource_id)
            records_by_id.update(records)

        # 校验所有请求的 evidence_id 都能在回源结果中找到
        missing = next(
            (
                item.evidence_id
                for item in evidence
                if item.evidence_id not in records_by_id
            ),
            None,
        )
        if missing is not None:
            raise EvidenceNotFoundError(missing)
        # 按输入顺序返回，保持调用方期望的排序
        return [records_by_id[item.evidence_id] for item in evidence]
