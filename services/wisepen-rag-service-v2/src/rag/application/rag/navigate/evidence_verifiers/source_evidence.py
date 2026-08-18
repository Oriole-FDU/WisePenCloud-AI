"""VERIFY 用例：校验候选检索块并返回当前已发布的原文证据。"""

from collections.abc import Sequence

from rag.domain.models.provenance import SourceEvidence
from rag.domain.models.retrieval import RetrievalCandidate
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedResourceCorruptError,
    PublishedResourceReader,
    PublishedResourceRevisionError,
)


class EvidenceNotFoundError(RuntimeError):
    """请求的已发布内容或证据身份不存在。"""


class EvidenceRevisionError(RuntimeError):
    """请求 revision 不是资源当前发布版本。"""


class EvidenceCorruptError(RuntimeError):
    """权威原文、SourceRef 或结构记录不满足一致性约束。"""


class SourceEvidenceVerifier:
    """按 SourceRef 身份回源，并逐项校验候选字段归属。"""

    __slots__ = ("_reader",)

    def __init__(self, *, reader: PublishedResourceReader) -> None:
        self._reader = reader

    async def verify_retrieval_candidates(
        self,
        candidates: Sequence[RetrievalCandidate],
    ) -> list[SourceEvidence]:
        """回源并验证检索候选与已发布证据的完整身份链。"""
        if not candidates:
            return []

        # 候选已由调用方按 (resource_id, content_revision) 分组后逐组调用，
        # 直接取组内身份回源到权威存储。
        resource_id = candidates[0].resource_id
        content_revision = candidates[0].content_revision
        try:
            records = await self._reader.get_source_evidence(
                resource_id,
                content_revision,
                list(
                    dict.fromkeys(candidate.source_ref_id for candidate in candidates)
                ),
            )
        except PublishedResourceRevisionError as error:
            raise EvidenceRevisionError(str(error)) from error
        except PublishedResourceCorruptError as error:
            raise EvidenceCorruptError(str(error)) from error
        if records is None:
            raise EvidenceNotFoundError(resource_id)

        # 逐项校验候选与回源记录的一致性
        verified: list[SourceEvidence] = []
        for candidate in candidates:
            record = records.get(candidate.source_ref_id)
            if record is None:
                raise EvidenceNotFoundError(candidate.source_ref_id)
            self._verify_candidate(candidate, record)
            verified.append(record)
        return verified

    @staticmethod
    def _verify_candidate(
        candidate: RetrievalCandidate,
        record: SourceEvidence,
    ) -> None:
        source_ref = record.source_ref
        # 1. 资源与版本校验：确保候选属于正确的资源和 revision
        if source_ref.resource_id != candidate.resource_id:
            raise EvidenceRevisionError(
                f"source ref {source_ref.ref_id} has invalid resource"
            )
        if source_ref.content_revision != candidate.content_revision:
            raise EvidenceRevisionError(
                f"source ref {source_ref.ref_id} has invalid revision"
            )
        # 2. 身份链校验：确保 ref_id、chunk_id、reading_block_id、section_id 一致
        if source_ref.ref_id != candidate.source_ref_id:
            raise EvidenceCorruptError(
                "evidence reader returned a mismatched source ref"
            )
        if candidate.chunk_id != source_ref.chunk_id:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} does not match source ref"
            )
        if candidate.reading_block_id != source_ref.reading_block_id:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} has invalid reading block"
            )
        if candidate.section_id != source_ref.section_id:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} has invalid section"
            )
        # 3. 结构元数据校验：确保 section_path、source_spans、页码、锚点等一致
        if candidate.section_path != source_ref.section_path:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} has invalid section path"
            )
        if candidate.source_spans != source_ref.source_spans:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} has invalid source spans"
            )
        if candidate.page_labels != source_ref.page_labels:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} has invalid page labels"
            )
        if candidate.anchor_labels != source_ref.anchor_labels:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} has invalid anchor labels"
            )
        # 4. 引用完整性校验：确保回源记录中的 ReadingBlock 和 Section 与 source_ref 一致
        if record.reading_block.block_id != source_ref.reading_block_id:
            raise EvidenceCorruptError(
                f"source ref {source_ref.ref_id} has invalid block record"
            )
        if record.reading_block.section_id != source_ref.section_id:
            raise EvidenceCorruptError(
                f"source ref {source_ref.ref_id} has invalid block owner"
            )
        if record.section.section_id != source_ref.section_id:
            raise EvidenceCorruptError(
                f"source ref {source_ref.ref_id} has invalid section record"
            )
        # 5. 正文一致性校验：确保候选文本与权威存储的原文完全一致
        if candidate.raw_text != record.source_text:
            raise EvidenceCorruptError(
                f"chunk {candidate.chunk_id} does not match authoritative text"
            )
