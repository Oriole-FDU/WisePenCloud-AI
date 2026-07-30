from typing import Any

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends

from common.core.domain import R
from common.core.exceptions import ServiceException
from common.security import SecurityContextHolder, require_login
from rag.api.schemas import ExpandRequest, LocateRequest, ReadSectionsRequest
from rag.application.rag.evidence import RagMaterializedSource
from rag.application.rag.ingestion import RagSectionNode, RagSectionReadingBlock
from rag.application.rag.knowledge_navigation import (
    KnowledgeNavigationExpandResult,
    KnowledgeNavigationLocateResult,
    KnowledgeNavigationService,
    KnowledgeNavigationStateInvalidatedError,
    KnowledgeNavigationStateNotFoundError,
    KnowledgeSectionReadResult,
)
from rag.application.rag.retrieval import RagPermissionScope, RagRetrievalError
from rag.application.rag.section_navigation import RagSectionView
from rag.container import Container
from rag.domain.error_codes import RagErrorCode

router = APIRouter()


@router.post("/locate", response_model=R[dict[str, Any]])
@inject
async def locate(
    request: LocateRequest,
    user_id: str = Depends(require_login),
    service: KnowledgeNavigationService = Depends(
        Provide[Container.knowledge_navigation_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.locate(
            query=request.query.strip(),
            max_results=request.max_results,
            session_id=request.session_id,
            permission_scope=_permission_scope(user_id),
        )
    except RagRetrievalError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_INVALID, str(error)) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED, str(error)) from error
    return R.success(_locate_payload(result))


@router.post("/expand", response_model=R[dict[str, Any]])
@inject
async def expand(
    request: ExpandRequest,
    user_id: str = Depends(require_login),
    service: KnowledgeNavigationService = Depends(
        Provide[Container.knowledge_navigation_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.expand(
            state_id=request.state_id,
            node_ids=request.node_ids,
            query=request.query.strip() if request.query else None,
            relation_types=request.relation_types,
            direction=request.direction,
            max_depth=request.max_depth,
            max_results=request.max_results,
            session_id=request.session_id,
            permission_scope=_permission_scope(user_id),
        )
    except KnowledgeNavigationStateNotFoundError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_NOT_FOUND) from error
    except KnowledgeNavigationStateInvalidatedError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_INVALIDATED) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED, str(error)) from error
    return R.success(_expand_payload(result))


@router.post("/sections", response_model=R[dict[str, Any]])
@inject
async def read_sections(
    request: ReadSectionsRequest,
    user_id: str = Depends(require_login),
    service: KnowledgeNavigationService = Depends(
        Provide[Container.knowledge_navigation_service]
    ),
) -> R[dict[str, Any]]:
    try:
        result = await service.read_sections(
            state_id=request.state_id,
            section_ids=request.section_ids,
            session_id=request.session_id,
            permission_scope=_permission_scope(user_id),
        )
    except KnowledgeNavigationStateNotFoundError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_NOT_FOUND) from error
    except KnowledgeNavigationStateInvalidatedError as error:
        raise ServiceException(RagErrorCode.NAVIGATION_STATE_INVALIDATED) from error
    except Exception as error:
        raise ServiceException(RagErrorCode.NAVIGATION_FAILED, str(error)) from error
    return R.success(_sections_payload(result))


def _permission_scope(user_id: str) -> RagPermissionScope:
    return RagPermissionScope(
        user_id=user_id,
        group_role_map=SecurityContextHolder.get_group_role_map(),
    )


def _locate_payload(result: KnowledgeNavigationLocateResult) -> dict[str, Any]:
    return {
        "state_id": result.state_id,
        "nodes": [node.to_payload() for node in result.nodes],
        "sources": [_section_view_payload(source) for source in result.sources],
    }


def _expand_payload(result: KnowledgeNavigationExpandResult) -> dict[str, Any]:
    return {
        "state_id": result.state_id,
        "nodes": [node.to_payload() for node in result.nodes],
        "edges": [
            {
                "edge_id": edge.edge_id,
                "source_node_id": edge.source_node_id,
                "target_node_id": edge.target_node_id,
                "relation_type": edge.relation_type.value,
                "predicate": edge.predicate,
                "evidence_quotes": list(edge.evidence_quotes),
            }
            for edge in result.edges
        ],
        "paths": [path.to_payload() for path in result.paths],
        "sources": [_section_view_payload(source) for source in result.sources],
    }


def _sections_payload(result: KnowledgeSectionReadResult) -> dict[str, Any]:
    return {
        "state_id": result.state_id,
        "sections": [_section_view_payload(section) for section in result.sections],
    }


def _section_view_payload(view: RagSectionView) -> dict[str, Any]:
    return {
        "resource_id": view.section.resource_id,
        **view.section.to_tree_payload(),
        "reading_blocks": [
            _reading_block_payload(block) for block in view.reading_blocks
        ],
        "evidence": [_source_payload(source) for source in view.sources],
        "frontier": {
            "parent": _section_node_payload(view.parent),
            "previous": _section_node_payload(view.previous),
            "next": _section_node_payload(view.next),
            "children": [child.to_tree_payload() for child in view.children],
        },
    }


def _section_node_payload(section: RagSectionNode | None) -> dict[str, object] | None:
    return section.to_tree_payload() if section is not None else None


def _reading_block_payload(block: RagSectionReadingBlock) -> dict[str, Any]:
    return {
        "block_id": block.block_id,
        "section_id": block.section_id,
        "raw_text": block.raw_text,
        "page_labels": list(block.page_labels),
        "anchor_labels": list(block.anchor_labels),
    }


def _source_payload(source: RagMaterializedSource) -> dict[str, Any]:
    source_ref = source.source_ref
    return {
        "content": source.content,
        "ref_id": source_ref.ref_id,
        "resource_id": source_ref.resource_id,
        "section_id": source_ref.section_id,
        "section_path": list(source_ref.section_path),
        "chunk_id": source_ref.chunk_id,
        "page_label": source_ref.page_label,
        "anchor_labels": list(source_ref.anchor_labels),
    }
