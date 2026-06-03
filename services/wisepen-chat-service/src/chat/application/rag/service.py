from dataclasses import dataclass, fields
from typing import Dict, List, Optional

from chat.application.rag.domain.enums import InsufficientReason
from chat.application.rag.domain.ports import RagManifestRepository
from chat.application.rag.domain.resource_lifecycle import RagResource
from chat.application.rag.domain.retrieval_planning import RagRetrievalQuery
from chat.application.rag.implementations.resources.resource_service import ResourceService
from chat.application.rag.implementations.resources.version_service import RagVersionService
from chat.application.rag.implementations.retrieval.context_assembler import RagContextAssembler
from chat.application.rag.implementations.retrieval.retrieval_pipeline import (
    RagRetrievalPipeline,
)
from .enums import ResourceKind, RetrievalMode
from .errors import RagInvalidResourceKindError, RagResourceNotFoundError
from .models import (
    RagIndexManifestView,
    RagIndexReadiness,
    RagIndexRebuildResult,
    RagResourceDeleteResult,
    RagResourceRef,
    RagResourceUpsertCommand,
    RagResourceUpsertResult,
    RagResourceView,
    RagSearchRequest,
    RagSearchResult,
)


@dataclass(frozen=True, slots=True)
class ModeRetrievalDefaults:
    """表示当前组件。"""
    top_k: int
    fusion_top_k: int
    rerank_top_n: int
    final_top_k: int
    neighbor_before: int
    neighbor_after: int
    mmr_lambda: float
    semantic_query_limit: int
    keyword_query_limit: int


_MODE_DEFAULTS: Dict[RetrievalMode, ModeRetrievalDefaults] = {
    RetrievalMode.NORMAL: ModeRetrievalDefaults(
        top_k=30,
        fusion_top_k=50,
        rerank_top_n=30,
        final_top_k=8,
        neighbor_before=1,
        neighbor_after=1,
        mmr_lambda=0.72,
        semantic_query_limit=3,
        keyword_query_limit=3,
    ),
    RetrievalMode.SEMANTIC: ModeRetrievalDefaults(
        top_k=40,
        fusion_top_k=60,
        rerank_top_n=30,
        final_top_k=8,
        neighbor_before=1,
        neighbor_after=1,
        mmr_lambda=0.78,
        semantic_query_limit=5,
        keyword_query_limit=2,
    ),
    RetrievalMode.EXACT: ModeRetrievalDefaults(
        top_k=40,
        fusion_top_k=70,
        rerank_top_n=30,
        final_top_k=8,
        neighbor_before=0,
        neighbor_after=0,
        mmr_lambda=0.65,
        semantic_query_limit=2,
        keyword_query_limit=5,
    ),
}

_CFG_FIELDS = tuple(
    f.name for f in fields(ModeRetrievalDefaults) if "query_limit" not in f.name
)


class RagService:
    """Facade for RAG resources, indexing readiness, and retrieval."""

    def __init__(
        self,
        resource_service: ResourceService,
        version_service: RagVersionService,
        manifest_repository: RagManifestRepository,
        retrieval_pipeline: RagRetrievalPipeline,
        context_assembler: RagContextAssembler,
    ) -> None:

        self._resource_service = resource_service
        self._version_service = version_service
        self._manifest_repository = manifest_repository
        self._retrieval_pipeline = retrieval_pipeline
        self._context_assembler = context_assembler

    async def upsert_resource(
        self,
        command: RagResourceUpsertCommand,
    ) -> RagResourceUpsertResult:

        resource = RagResource(
            user_id=command.user_id,
            resource_kind=command.resource_kind,
            resource_id=command.resource_id,
            content=command.content,
            title=command.title,
            document_name=command.document_name,
        )
        result = await self._resource_service.upsert(resource)
        return RagResourceUpsertResult(
            resource_id=result.resource.resource_id,
            resource_kind=result.resource.resource_kind,
            resource_version=result.version_snapshot.resource_version,
            material_hash=result.version_snapshot.material_hash,
            pipeline_version=result.version_snapshot.pipeline_version,
            index_version=result.version_snapshot.index_version,
            indexing_message_published=True,
        )

    async def delete_resource(self, ref: RagResourceRef) -> RagResourceDeleteResult:

        deleted_resource = await self._resource_service.delete(
            resource_kind=ref.resource_kind,
            user_id=ref.user_id,
            resource_id=ref.resource_id,
        )
        return RagResourceDeleteResult(
            resource_id=ref.resource_id,
            resource_kind=ref.resource_kind,
            deleted=deleted_resource is not None,
        )

    async def get_resource(self, ref: RagResourceRef) -> RagResourceView:

        resource = await self._resource_service.get(
            resource_kind=ref.resource_kind,
            user_id=ref.user_id,
            resource_id=ref.resource_id,
        )
        if resource is None:
            raise RagResourceNotFoundError()
        return RagResourceView(
            resource_id=resource.resource_id,
            resource_kind=resource.resource_kind,
            version=resource.version,
            content=resource.content,
            is_deleted=resource.is_deleted,
        )

    async def get_index_manifest(
        self,
        ref: RagResourceRef,
    ) -> Optional[RagIndexManifestView]:

        manifest = await self._manifest_repository.get_by_resource(
            user_id=ref.user_id,
            resource_kind=ref.resource_kind,
            resource_id=ref.resource_id,
        )
        if manifest is None:
            return None
        return RagIndexManifestView(
            resource_id=manifest.resource_id,
            resource_kind=manifest.resource_kind,
            resource_version=manifest.resource_version,
            material_hash=manifest.material_hash,
            pipeline_version=manifest.pipeline_version,
            current_index_version=manifest.current_index_version,
        )

    async def get_index_readiness(self, ref: RagResourceRef) -> RagIndexReadiness:

        resource = await self._resource_service.get(
            resource_kind=ref.resource_kind,
            user_id=ref.user_id,
            resource_id=ref.resource_id,
        )
        if resource is None:
            raise RagResourceNotFoundError()

        snapshot = self._version_service.build_snapshot(resource)
        manifest = await self._manifest_repository.get_by_resource(
            user_id=ref.user_id,
            resource_kind=ref.resource_kind,
            resource_id=ref.resource_id,
        )

        current_index_version = (
            manifest.current_index_version if manifest is not None else None
        )
        is_index_current = current_index_version == snapshot.index_version

        return RagIndexReadiness(
            resource_id=resource.resource_id,
            resource_kind=resource.resource_kind,
            target_index_version=snapshot.index_version,
            current_index_version=current_index_version,
            is_index_current=is_index_current,
            needs_indexing=not is_index_current,
            can_retrieve_published_index=manifest is not None,
            indexing_message_published=not is_index_current,
        )

    async def rebuild_index(self, ref: RagResourceRef) -> RagIndexRebuildResult:

        resource = await self._resource_service.get(
            resource_kind=ref.resource_kind,
            user_id=ref.user_id,
            resource_id=ref.resource_id,
        )
        if resource is None:
            raise RagResourceNotFoundError()

        result = await self._resource_service.upsert(resource)
        return RagIndexRebuildResult(
            resource_id=result.resource.resource_id,
            resource_kind=result.resource.resource_kind,
            resource_version=result.version_snapshot.resource_version,
            target_index_version=result.version_snapshot.index_version,
            indexing_message_published=True,
        )

    async def search(self, request: RagSearchRequest) -> RagSearchResult:

        retrieval_query = self._build_retrieval_query(request)
        result = await self._retrieval_pipeline.retrieve(retrieval_query)
        assembled_context = self._context_assembler.assemble(list(result.evidences))
        rendered_text = _render_evidence_pack(
            result=result,
            assembled_context_text=assembled_context.text,
            included_evidence_ids=assembled_context.included_evidence_ids,
            skipped_evidence_count=assembled_context.skipped_evidence_count,
        )
        reason = result.sufficiency.reason
        return RagSearchResult(
            query=result.query.query,
            mode=result.query.mode,
            evidence_count=len(result.evidences),
            sufficient=result.sufficiency.sufficient,
            insufficient_reason=reason.value if reason is not None else None,
            included_evidence_ids=assembled_context.included_evidence_ids,
            skipped_evidence_count=assembled_context.skipped_evidence_count,
            assembled_context=assembled_context.text,
            rendered_text=rendered_text,
        )

    def _build_retrieval_query(self, request: RagSearchRequest) -> RagRetrievalQuery:

        stripped_query = request.query.strip()
        if not stripped_query:
            raise ValueError("query must not be empty or blank.")

        mode_defaults = _MODE_DEFAULTS[request.mode]
        semantic_queries = [
            q.strip() for q in (request.semantic_queries or []) if q.strip()
        ]
        keyword_queries = [
            q.strip() for q in (request.keyword_queries or []) if q.strip()
        ]

        if (
            len(semantic_queries) > mode_defaults.semantic_query_limit
            or len(keyword_queries) > mode_defaults.keyword_query_limit
        ):
            raise ValueError(
                f"Query variant count exceeds the limit allowed in {request.mode.value} mode."
            )

        cfg_args = {
            field_name: (
                getattr(request, field_name)
                if getattr(request, field_name) is not None
                else getattr(mode_defaults, field_name)
            )
            for field_name in _CFG_FIELDS
        }

        return RagRetrievalQuery(
            user_id=request.user_id,
            query=stripped_query,
            mode=request.mode,
            resource_kinds=request.resource_kinds,
            semantic_queries=semantic_queries,
            keyword_queries=keyword_queries,
            **cfg_args,
        )


def parse_resource_kind(raw: str) -> ResourceKind:
    try:
        return ResourceKind(raw)
    except ValueError:
        raise RagInvalidResourceKindError(f"Unsupported resource_kind: {raw}") from None


def _render_evidence_pack(
    *,
    result,
    assembled_context_text: str,
    included_evidence_ids: List[str],
    skipped_evidence_count: int,
) -> str:

    is_sufficient = result.sufficiency.sufficient
    reason_str = (
        result.sufficiency.reason.value
        if result.sufficiency.reason is not None
        else "none"
    )
    policy_msg = _answer_policy_message(result.sufficiency.reason)

    if not result.evidences:
        return (
            "[Tool Result] RAG search evidence pack\n"
            f"Query: {result.query.query}\n"
            f"Mode: {result.query.mode.value}\n"
            "Evidence count: 0\n"
            f"Sufficient: {str(is_sufficient).lower()}\n"
            f"Insufficient reason: {reason_str}\n"
            "\n"
            "Answerability:\n"
            f"- can_answer: {str(is_sufficient).lower()}\n"
            f"- refusal_required: {str(not is_sufficient).lower()}\n"
            f"- policy: {policy_msg}\n"
            "\n"
            "No indexed evidence was found for this query."
        )

    lines = [
        "[Tool Result] RAG search evidence pack",
        f"Query: {result.query.query}",
        f"Mode: {result.query.mode.value}",
        f"Evidence count: {len(result.evidences)}",
        f"Sufficient: {str(is_sufficient).lower()}",
        f"Insufficient reason: {reason_str}",
        f"Included evidence count: {len(included_evidence_ids)}",
        f"Skipped evidence count: {skipped_evidence_count}",
        "",
        "Answerability:",
        f"- can_answer: {str(is_sufficient).lower()}",
        f"- refusal_required: {str(not is_sufficient).lower()}",
        f"- policy: {policy_msg}",
        "",
        "Result order: reranked, parent-aggregated, and MMR-selected.",
        (
            "Tool-use note: use only the assembled context below for grounded answers. "
            "If refusal_required is true, do not answer as if the indexed evidence_access base supports the claim."
        ),
        "",
        "Assembled context:",
        assembled_context_text,
    ]
    return "\n".join(lines).strip()


def _answer_policy_message(reason: Optional[InsufficientReason]) -> str:

    if reason is None:
        return (
            "The indexed evidence_access base contains sufficient retrieval evidence. "
            "Answer using only the assembled context."
        )

    if reason == InsufficientReason.NO_RESULTS:
        return (
            "No indexed evidence was found. Refuse to answer from the user's evidence_access base; "
            "say that the indexed notes/documents do not contain relevant information."
        )

    if reason == InsufficientReason.EXACT_MODE_NO_KEYWORD_HIT:
        return (
            "Exact mode did not find a keyword_exact hit. Do not claim an exact match exists; "
            "say that no exact indexed match was found."
        )

    if reason == InsufficientReason.LOW_SCORE:
        return (
            "The retrieved evidence is weak. Do not give a definitive answer; "
            "say that the indexed evidence_access base does not contain enough reliable evidence."
        )

    raise ValueError(f"Unsupported insufficient reason: {reason}")
