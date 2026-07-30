from typing import Annotated, Any

from common.core.exceptions import RpcError, ServiceException
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field, StringConstraints

from wisepen_mcp.capabilities.core.tools import MCP_TOOL_CONTEXT_META_KEY
from wisepen_mcp.domain.error_codes import McpErrorCode
from wisepen_mcp.service_client import RagServiceClient

from .models import (
    KnowledgeNavigationDirection,
    KnowledgeRelationType,
)
from .renderer import (
    render_expand_result,
    render_locate_result,
    render_sections_result,
)

LocateQuery = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(
        description=(
            "The complete question or concept to answer from the user's private "
            "documents. Include the subject and constraints needed to judge relevance."
        ),
    ),
]
StateId = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1),
    Field(
        description="Reuse the exact state_id returned by locate or an earlier navigation call.",
    ),
]
NodeIds = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1,
        max_length=16,
        description=(
            "Node IDs already returned in this navigation state. Each ID is a graph "
            "expansion seed; do not invent IDs from labels."
        ),
    ),
]
SectionIds = Annotated[
    tuple[Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)], ...],
    Field(
        min_length=1,
        max_length=12,
        description=(
            "Section IDs already returned in sources or frontier entries. Select the "
            "sections whose full reading blocks are needed."
        ),
    ),
]

LOCATE_DESCRIPTION = (
    "Description:\n"
    "Locate evidence and concepts in the user's private WisePen documents. Use this "
    "as the first private-knowledge navigation call.\n\n"
    "Output:\n"
    "Sources contain grounded evidence; use content_index to access exact text. Nodes "
    "are navigation anchors, not evidence. Reuse state_id with expand or sections."
)
EXPAND_DESCRIPTION = (
    "Description:\n"
    "Follow semantic relations from nodes returned by locate or an earlier expand. "
    "Candidate paths are generated from graph constraints and ranked by query.\n\n"
    "Output:\n"
    "Treat each path as a candidate reasoning chain. Use relation_evidence for the "
    "readable relation statement and quotes, then ground claims in returned sources."
)
SECTIONS_DESCRIPTION = (
    "Description:\n"
    "Read selected private-document sections and reveal parent, previous, next, and "
    "child navigation choices.\n\n"
    "Output:\n"
    "Use reading blocks and evidence as document context. Frontier entries indicate "
    "where to continue reading and do not themselves prove semantic relations."
)


def register_rag_tools(mcp: FastMCP, client: RagServiceClient) -> None:
    @mcp.tool(name="knowledge_navigate_locate", description=LOCATE_DESCRIPTION)
    async def knowledge_navigate_locate(
        query: LocateQuery,
        ctx: Context,
        max_results: Annotated[
            int,
            Field(
                ge=1,
                le=20,
                description="Maximum number of relevant private-document results to return.",
            ),
        ] = 10,
    ) -> dict[str, Any]:
        try:
            result = await client.locate(
                session_id=_session_id(ctx),
                query=query,
                max_results=max_results,
            )
        except RpcError as error:
            raise _service_exception(error) from error
        return render_locate_result(result)

    @mcp.tool(name="knowledge_navigate_expand", description=EXPAND_DESCRIPTION)
    async def knowledge_navigate_expand(
        state_id: StateId,
        node_ids: NodeIds,
        ctx: Context,
        query: Annotated[
            str | None,
            StringConstraints(strip_whitespace=True, min_length=1),
            Field(
                description=(
                    "Optional intent for ranking graph paths after relation-constrained "
                    "candidates are generated. It does not alter graph traversal rules; "
                    "omit it to reuse the original locate query."
                )
            ),
        ] = None,
        relation_types: Annotated[
            tuple[KnowledgeRelationType, ...],
            Field(
                max_length=16,
                description=(
                    "Allowed formal relation types. Leave empty to allow every supported "
                    "relation type."
                ),
            ),
        ] = (),
        direction: Annotated[
            KnowledgeNavigationDirection,
            Field(
                description=(
                    "Traversal direction relative to each seed: out follows semantic "
                    "source-to-target edges, in follows edges whose target is the seed, "
                    "and both allows either direction."
                )
            ),
        ] = KnowledgeNavigationDirection.BOTH,
        max_depth: Annotated[
            int,
            Field(
                ge=1,
                le=2,
                description="Maximum relation hops per candidate path: 1 for direct, 2 for two-hop.",
            ),
        ] = 1,
        max_results: Annotated[
            int,
            Field(
                ge=1,
                le=20,
                description="Maximum number of ranked relation paths to return.",
            ),
        ] = 10,
    ) -> dict[str, Any]:
        try:
            result = await client.expand(
                session_id=_session_id(ctx),
                state_id=state_id,
                node_ids=node_ids,
                query=query,
                relation_types=tuple(value.value for value in relation_types),
                direction=direction.value,
                max_depth=max_depth,
                max_results=max_results,
            )
        except RpcError as error:
            raise _service_exception(error) from error
        return render_expand_result(result)

    @mcp.tool(name="knowledge_navigate_sections", description=SECTIONS_DESCRIPTION)
    async def knowledge_navigate_sections(
        state_id: StateId,
        section_ids: SectionIds,
        ctx: Context,
    ) -> dict[str, Any]:
        try:
            result = await client.read_sections(
                session_id=_session_id(ctx),
                state_id=state_id,
                section_ids=section_ids,
            )
        except RpcError as error:
            raise _service_exception(error) from error
        return render_sections_result(result)


def _session_id(ctx: Context) -> str:
    meta = ctx.request_context.meta
    tool_context: Any = None
    if meta:
        tool_context = (meta.model_extra or {}).get(MCP_TOOL_CONTEXT_META_KEY)
    session_id = tool_context.get("session_id") if isinstance(tool_context, dict) else None
    if not isinstance(session_id, str) or not session_id.strip():
        raise ServiceException(
            McpErrorCode.RAG_NAVIGATION_INVALID,
            "session_id is missing from MCP tool context.",
        )
    return session_id.strip()


def _service_exception(error: RpcError) -> ServiceException:
    if error.code == 42001:
        return ServiceException(McpErrorCode.RAG_NAVIGATION_INVALID, error.msg)
    if error.code == 42002:
        return ServiceException(McpErrorCode.RAG_NAVIGATION_STATE_NOT_FOUND)
    if error.code == 42003:
        return ServiceException(McpErrorCode.RAG_NAVIGATION_STATE_INVALIDATED)
    return ServiceException(McpErrorCode.RAG_NAVIGATION_FAILED, error.msg)
