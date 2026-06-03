from typing import Any, Dict

from chat.application.rag.enums import ResourceKind, RetrievalMode
from chat.application.rag.models import RagSearchRequest
from chat.application.rag.service import RagService
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail, log_ok

_TOOL_DESCRIPTION = (
    "Searches the user's indexed notes and documents with multi-channel RAG retrieval. "
    "It returns grounded evidence from the user's personal evidence_access base, including "
    "retrieval context, matched text, parent chunk text, neighbor context, ranking scores, "
    "and source metadata.\n\n"
    "Use rag_search when the user asks about their own notes, uploaded documents, indexed "
    "study materials, previous research materials, or information that should be grounded "
    "in their personal evidence_access base.\n\n"
    "Do not use rag_search for current web information, public facts, prices, laws, news, "
    "or external source discovery. Use web_search for those cases.\n\n"
    "The model must not provide user_id. user_id is injected by the server execution context. "
    "resource_kinds can be used to restrict retrieval to note or document resources when the "
    "user explicitly narrows the source type.\n\n"
    "Mode rules:\n"
    "- normal: balanced retrieval across semantic, lexical, and keyword channels.\n"
    "- exact: prioritize exact wording, identifiers, formulas, names, and precise snippets.\n"
    "- semantic: prioritize conceptual similarity and paraphrased matches.\n\n"
    "Return protocol: rag_search returns final evidence directly. The evidence is already "
    "retrieved, fused, parent-aggregated, reranked, MMR-selected, and context-assembled. "
    "The assistant should answer using the returned assembled context and should not invent "
    "unsupported facts.\n\n"
    "Answerability policy:\n"
    "- If Answerability.can_answer is true, answer using only the assembled context.\n"
    "- If Answerability.can_answer is false, do not provide a factual answer as if the indexed evidence_access base supports it.\n"
    "- When evidence is insufficient, say that the indexed notes/documents do not contain enough reliable information.\n"
    "- For public/current information, use web_search instead of guessing from RAG evidence."
)

_DEFAULT_MODE_STR = RetrievalMode.NORMAL.value

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {
            "type": "string",
            "minLength": 1,
            "maxLength": 4000,
            "description": "The user's core retrieval question or information need.",
        },
        "mode": {
            "type": "string",
            "enum": [mode.value for mode in RetrievalMode],
            "description": (
                "Retrieval strategy mode. Use 'normal' for general queries. "
                "Use 'semantic' explicitly when the query relies heavily on deep conceptual understanding or vector-space synonyms. "
                "Use 'exact' for strict, precise keyword or wording matches."
            ),
            "default": _DEFAULT_MODE_STR,
        },
        "resource_kinds": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [kind.value for kind in ResourceKind],
            },
            "minItems": 1,
            "maxItems": len(ResourceKind),
            "uniqueItems": True,
            "description": "Optional filter to restrict search to specific resource types. If omitted, all types are searched.",
        },
        "semantic_queries": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
            "minItems": 1,
            "maxItems": 5,
            "uniqueItems": True,
            "description": (
                "Optional semantic query variants for dense semantic retrieval. "
                "Provide only when multi-query rewriting is needed to cover distinct conceptual phrasings."
            ),
        },
        "keyword_queries": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 1000},
            "minItems": 1,
            "maxItems": 5,
            "uniqueItems": True,
            "description": (
                "Optional keyword query variants for BM25 and exact keyword retrieval. "
                "Provide only when distinct terms, identifiers, names, formulas, titles, or code symbols should be matched explicitly."
            ),
        },
        "top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Candidate chunk count retrieved per channel. If omitted, determined by mode.",
        },
        "fusion_top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 200,
            "description": "Candidate chunk count retained after Reciprocal Rank Fusion. If omitted, determined by mode.",
        },
        "rerank_top_n": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "description": "Candidate chunk count retained after reranking. If omitted, determined by mode.",
        },
        "final_top_k": {
            "type": "integer",
            "minimum": 1,
            "maximum": 30,
            "description": "Final consolidated evidence count returned to the assistant context. If omitted, determined by mode.",
        },
        "neighbor_before": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5,
            "description": "Number of parent neighbor chunks expanding upstream before the matched chunk. If omitted, determined by mode.",
        },
        "neighbor_after": {
            "type": "integer",
            "minimum": 0,
            "maximum": 5,
            "description": "Number of parent neighbor chunks expanding downstream after the matched chunk. If omitted, determined by mode.",
        },
        "mmr_lambda": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0,
            "description": "MMR relevance-diversity tradeoff weight. If omitted, determined by mode.",
        },
    },
    "required": ["query"],
    "additionalProperties": False,
}


class RagSearchTool(BaseTool):
    """RAG retrieval tool facade adapter."""

    def __init__(self, rag_service: RagService) -> None:
        self._rag_service = rag_service

    @property
    def name(self) -> str:
        return "rag_search"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        user_id = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."
        session_id = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        try:
            search_request = RagSearchRequest(
                user_id=str(user_id),
                query=str(kwargs.get("query", "")),
                mode=RetrievalMode(kwargs.get("mode", _DEFAULT_MODE_STR)),
                resource_kinds=[
                    ResourceKind(kind) for kind in kwargs.get("resource_kinds", [])
                ],
                semantic_queries=kwargs.get("semantic_queries", []),
                keyword_queries=kwargs.get("keyword_queries", []),
                top_k=kwargs.get("top_k"),
                fusion_top_k=kwargs.get("fusion_top_k"),
                rerank_top_n=kwargs.get("rerank_top_n"),
                final_top_k=kwargs.get("final_top_k"),
                neighbor_before=kwargs.get("neighbor_before"),
                neighbor_after=kwargs.get("neighbor_after"),
                mmr_lambda=kwargs.get("mmr_lambda"),
            )
        except ValueError as e:
            return f"[Tool Error] {str(e)}"

        try:
            result = await self._rag_service.search(search_request)
        except ValueError as e:
            return f"[Tool Error] {str(e)}"
        except Exception as e:
            log_fail(
                "rag_search",
                repr(e),
                session_id=session_id,
                user_id=user_id,
                mode=search_request.mode.value,
            )
            return "[Tool Error] Unexpected error while searching indexed RAG evidence."

        log_ok(
            "rag_search",
            session_id=session_id,
            user_id=user_id,
            mode=result.mode.value,
            evidence_count=result.evidence_count,
        )

        return result.rendered_text
