import json
import re
from pathlib import PurePath
from typing import Any, Dict, List, Optional

from chat.application.infra.content_store.formatting import format_tool_content_receipt
from chat.application.infra.content_store.models import ContentReceipt
from chat.application.infra.document_temp_files.errors import (
    InvalidDocumentRefError,
    UnreadableDocumentRefError,
)
from chat.application.infra.document_temp_files.processing_scope import document_processing_scope
from chat.application.infra.document_temp_files.resolver import DocumentTempFileResolver
from chat.application.tools.common.evidence_ranking.models import RankedEvidence
from chat.application.tools.common.evidence_ranking.ranking import rank_evidence
from chat.application.tools.document.services.document_parse import (
    DocumentParseService,
)
from chat.application.tools.document.services.document_parse.models import DocumentParseResultItem
from chat.application.tools.tool_content_store import ToolContentStore
from chat.application.tools.tool_content_store import CONTENT_ROLE_PARSED, CONTENT_ROLE_WINDOW
from chat.domain.interfaces.tool import BaseTool
from common.logger import log_fail

_DOCUMENT_EVIDENCE_LIMIT_PER_FILE = 3
_INDEX_PREVIEW_LIMIT = 12
_ANCHOR_PREVIEW_LIMIT = 12

_TOOL_DESCRIPTION = (
    "Parses local or cached binary document files referenced by file_ref into "
    "Markdown text and structured tables. Only values explicitly labeled file_ref "
    "are valid inputs.\n\n"
    "Always pass all selected file_refs in one populated file_refs array.\n"
    "Never call document_parse once per file_ref for the same task.\n"
    "Never issue parallel document_parse calls for the same task.\n"
    "Never pass ToolContent content_id values such as cnt_* to document_parse.\n"
    "Never pass URLs to document_parse.\n\n"
    "Supported formats: PDF, DOCX, DOCM, PPTX, PPTM, EPUB, XLSX, XLS, XLSM, and ODS. "
    "Unsupported: HTML, TXT, MD, CSV, JSON, XML, images, audio, and video.\n\n"
    "document_parse returns complete parsed Markdown as the raw tool result. "
    "When objective is provided, document_parse also caches each parsed document "
    "as ToolContent and returns objective-relevant evidence snippets from the parsed "
    "Markdown. Use objective for the user's specific evidence goal, not a generic "
    "phrase.\n\n"
    "When more context is needed around returned evidence, call tool_content_read "
    "with the returned parsed_content_id and chunk_index. For second-pass reranking "
    "across parsed_content_id values, use evidence_rank.\n"
    "Do not pass file_ref values to evidence_rank or tool_content_read."
)

_TOOL_SCHEMA = {
    "type": "object",
    "properties": {
        "file_refs": {
            "type": "array",
            "items": {"type": "string", "minLength": 1},
            "minItems": 1,
            "description": (
                "One array of file_ref values. Do not pass content_id/cnt_* values."
            ),
        },
        "objective": {
            "type": "string",
            "minLength": 1,
            "description": (
                "Optional evidence extraction goal. When provided, document_parse "
                "caches parsed Markdown per file and returns ranked evidence snippets "
                "relevant to this goal."
            ),
        },
    },
    "required": ["file_refs"],
    "additionalProperties": False,
}


class DocumentParseTool(BaseTool):

    def __init__(
        self,
        *,
        parse_service: DocumentParseService,
        temp_file_resolver: DocumentTempFileResolver,
        content_store: ToolContentStore,
    ):
        self.parse_service = parse_service
        self.temp_file_resolver = temp_file_resolver
        self.content_store = content_store

    @property
    def name(self) -> str:
        return "document_parse"

    @property
    def description(self) -> str:
        return _TOOL_DESCRIPTION

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return _TOOL_SCHEMA

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """执行工具入口流程。"""
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."
        user_id: Optional[str] = context.get("user_id")
        if not user_id:
            return "[Tool Error] Missing user_id in execution context."

        file_refs: List[str] = kwargs["file_refs"]
        raw_objective: Optional[str] = kwargs.get("objective")
        objective = raw_objective.strip() if raw_objective else None

        for file_ref in file_refs:
            if file_ref.startswith("cnt_"):
                return (
                    "[Tool Error] Invalid file_refs parameter: content_id values "
                    "must be passed to tool_content_read or evidence_rank, not "
                    "document_parse."
                )
            if file_ref.startswith(("http://", "https://")):
                return (
                    "[Tool Error] Invalid file_refs parameter: URLs must be passed "
                    "to web_fetch, not document_parse."
                )

        resolved_paths = []
        for file_ref in file_refs:
            try:
                resolved = self.temp_file_resolver.resolve(
                    file_ref=file_ref,
                    user_id=user_id,
                    session_id=session_id,
                )
                resolved_paths.append((file_ref, resolved.path))
            except (InvalidDocumentRefError, UnreadableDocumentRefError):
                resolved_paths.append((file_ref, None))

        valid_paths = [p for _, p in resolved_paths if p is not None]
        valid_refs = [ref for ref, p in resolved_paths if p is not None]
        failed_resolutions = [(ref, p) for ref, p in resolved_paths if p is None]

        results: List[DocumentParseResultItem] = []
        if valid_paths:
            with document_processing_scope(
                self.temp_file_resolver.session_root(
                    user_id=user_id,
                    session_id=session_id,
                )
            ):
                results = await self.parse_service.parse_many(
                    valid_paths, file_refs=valid_refs
                )

        return self._format_batch_result(
            session_id=session_id,
            results=results,
            failed_resolutions=failed_resolutions,
            objective=objective,
        )

    def _format_batch_result(
        self,
        *,
        session_id: str,
        results: List[DocumentParseResultItem],
        failed_resolutions: List[tuple],
        objective: Optional[str],
    ) -> str:

        lines: List[str] = ["[Tool Result] Document parse batch results"]

        success_count = sum(1 for r in results if r.success)
        fail_count = len(results) - success_count + len(failed_resolutions)
        total = len(results) + len(failed_resolutions)
        lines.append(
            f"Total: {total} files, {success_count} succeeded, {fail_count} failed."
        )

        for ref, _ in failed_resolutions:
            display_name = ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
            lines.append("")
            lines.append(f"--- File: {display_name} ---")
            lines.append("[Parse Error] Document file not found.")

        for item in results:
            lines.append("")
            display_name = (
                item.file_ref.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] or "document"
            )
            lines.append(f"--- File: {display_name} ---")

            if item.success and item.result is not None:
                result = item.result
                receipt: Optional[ContentReceipt] = None
                if objective:
                    receipt = self.content_store.put_receipt(
                        session_id=session_id,
                        tool_name=self.name,
                        source=item.file_ref,
                        text=result.text,
                        content_type="text/markdown",
                        metadata=_build_document_metadata(
                            item=item,
                            objective=objective,
                        ),
                    )
                    if receipt is not None and receipt.cached:
                        stored = self.content_store.get(
                            session_id=session_id,
                            content_id=receipt.content_id,
                        )
                        protocol_metadata = {
                            "content_role": CONTENT_ROLE_PARSED,
                            "canonical_content_id": receipt.content_id,
                            "parsed_content_id": receipt.content_id,
                        }
                        if stored is not None:
                            chunk_ranges = [
                                (chunk.index, chunk.start_offset, chunk.end_offset)
                                for chunk in stored.chunks
                            ]
                            parsed_indices = _build_parsed_content_indices(
                                result=result,
                                parsed_content_id=receipt.content_id,
                                chunk_ranges=chunk_ranges,
                            )
                            index_receipt = self.content_store.put_receipt(
                                session_id=session_id,
                                tool_name=f"{self.name}_index",
                                source=item.file_ref,
                                text=json.dumps(parsed_indices, ensure_ascii=False),
                                content_type="application/json",
                                metadata={
                                    "content_role": CONTENT_ROLE_WINDOW,
                                    "content_kind": "document_parse_index",
                                    "canonical_content_id": receipt.content_id,
                                    "parsed_content_id": receipt.content_id,
                                    "title": stored.metadata.get("title") or display_name,
                                },
                            )
                            protocol_metadata.update(
                                _build_index_preview_metadata(
                                    indices=parsed_indices,
                                    full_index_content_id=(
                                        index_receipt.content_id
                                        if index_receipt is not None and index_receipt.cached
                                        else None
                                    ),
                                )
                            )
                        self.content_store.update_metadata(
                            session_id=session_id,
                            content_id=receipt.content_id,
                            metadata=protocol_metadata,
                        )
                        receipt.metadata.update(protocol_metadata)

                lines.append("[Parse Success]")
                lines.append(f"Source: {result.source}")
                lines.append(f"File type: {result.file_type}")
                lines.append(f"Pages: {len(result.pages)}")
                lines.append(f"Tables: {len(result.tables)}")

                if result.warnings:
                    lines.append(
                        f"Warnings: {result.warnings}"
                    )

                if result.metadata:
                    lines.append(
                        f"Metadata: {result.metadata}"
                    )

                if objective:
                    parsed_content_id = (
                        receipt.content_id if receipt is not None and receipt.cached else None
                    )
                    lines.extend(
                        _format_document_evidence_lines(
                            session_id=session_id,
                            parsed_content_id=parsed_content_id,
                            wrapper_content_id=None,
                            canonical_content_id=parsed_content_id,
                            result=result,
                            cache_error=(
                                receipt.cache_error
                                if receipt is not None
                                else "failed_to_cache_parsed_document"
                            ),
                            objective=objective,
                            content_store=self.content_store,
                        )
                    )

                    if receipt is not None:
                        lines.append("")
                        lines.append(format_tool_content_receipt(receipt))
                    else:
                        lines.extend(
                            [
                                "",
                                "[Parsed Markdown]",
                                "omitted: true",
                                "reason: Parsed Markdown could not be cached; use the parse metadata and error notes above.",
                            ]
                        )
                else:
                    lines.append("")
                    lines.append("[Parsed Markdown]")
                    lines.append(result.text)
            else:
                lines.append(f"[Parse Error] {item.error}")

        return "\n".join(lines)


def _build_document_metadata(
    *,
    item: DocumentParseResultItem,
    objective: str,
) -> Dict[str, Any]:
    result = item.result
    source = result.source if result is not None else item.file_ref
    title = PurePath(source).name or PurePath(item.file_ref).name or "document"
    metadata: Dict[str, Any] = {
        "content_role": CONTENT_ROLE_PARSED,
        "content_kind": "document_parse_markdown",
        "file_ref": item.file_ref,
        "title": title,
        "display_name": title,
        "source": source,
        "objective": objective,
    }
    if result is not None:
        metadata.update(
            {
                "file_type": str(result.file_type),
                "page_count": len(result.pages),
                "table_count": len(result.tables),
            }
        )
    return metadata


def _build_parsed_content_indices(
    *,
    result,
    parsed_content_id: str,
    chunk_ranges: List[tuple],
) -> Dict[str, Any]:
    page_chunk_map = _build_page_chunk_map(
        result=result,
        parsed_content_id=parsed_content_id,
        chunk_ranges=chunk_ranges,
    )
    section_map = _build_section_map(
        text=result.text,
        chunk_ranges=chunk_ranges,
    )
    return {
        "page_chunk_map": page_chunk_map,
        "section_map": section_map,
        "anchors": _build_document_anchors(
            text=result.text,
            page_chunk_map=page_chunk_map,
            chunk_ranges=chunk_ranges,
        ),
    }


def _build_index_preview_metadata(
    *,
    indices: Dict[str, Any],
    full_index_content_id: Optional[str],
) -> Dict[str, Any]:
    metadata: Dict[str, Any] = {
        "page_chunk_map_preview": indices.get("page_chunk_map", [])[:_INDEX_PREVIEW_LIMIT],
        "section_map_preview": indices.get("section_map", [])[:_INDEX_PREVIEW_LIMIT],
        "anchor_preview": indices.get("anchors", [])[:_ANCHOR_PREVIEW_LIMIT],
    }
    if full_index_content_id:
        metadata["full_index_content_id"] = full_index_content_id
    return metadata


def _build_page_chunk_map(
    *,
    result,
    parsed_content_id: str,
    chunk_ranges: List[tuple],
) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    cursor = 0

    for page in result.pages:
        page_text = page.text or ""
        if not page_text:
            continue

        start = result.text.find(page_text, cursor)
        if start < 0:
            continue
        end = start + len(page_text)
        cursor = end

        chunk_indices = _chunk_indices_for_range(
            start=start,
            end=end,
            chunk_ranges=chunk_ranges,
        )
        if chunk_indices:
            items.append(
                {
                    "page": page.page_index + 1,
                    "chunk_indices": chunk_indices,
                    "content_id": parsed_content_id,
                }
            )

    return items


def _build_section_map(
    *,
    text: str,
    chunk_ranges: List[tuple],
) -> List[Dict[str, Any]]:
    headings: List[Dict[str, Any]] = []
    offset = 0
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        if stripped.startswith("#"):
            marker = stripped.split(" ", 1)[0]
            if marker and set(marker) == {"#"} and 1 <= len(marker) <= 6:
                title = stripped[len(marker):].strip()
                if title:
                    headings.append(
                        {
                            "title": title,
                            "level": len(marker),
                            "start_offset": offset,
                        }
                    )
        offset += len(line)

    sections: List[Dict[str, Any]] = []
    for index, heading in enumerate(headings):
        start = heading["start_offset"]
        end = (
            headings[index + 1]["start_offset"]
            if index + 1 < len(headings)
            else len(text)
        )
        chunk_indices = _chunk_indices_for_range(
            start=start,
            end=end,
            chunk_ranges=chunk_ranges,
        )
        if not chunk_indices:
            continue
        sections.append(
            {
                "title": heading["title"],
                "level": heading["level"],
                "start_chunk_index": chunk_indices[0],
                "end_chunk_index": chunk_indices[-1],
            }
        )

    return sections


def _build_document_anchors(
    *,
    text: str,
    page_chunk_map: List[Dict[str, Any]],
    chunk_ranges: List[tuple],
) -> List[Dict[str, Any]]:
    pattern = re.compile(
        r"\b(?P<label>(?P<kind>Table|Figure|Fig\.|Equation|Eq\.)\s+[A-Za-z0-9]+)",
        re.IGNORECASE,
    )
    anchors: List[Dict[str, Any]] = []
    seen = set()
    offset = 0

    for paragraph_index, line in enumerate(text.splitlines(keepends=True), 1):
        stripped = line.strip()
        if not stripped:
            offset += len(line)
            continue

        match = pattern.search(stripped)
        if match is None:
            offset += len(line)
            continue

        label = match.group("label").replace("Fig.", "Figure").replace("Eq.", "Equation")
        key = (label.lower(), stripped[:120])
        if key in seen:
            offset += len(line)
            continue
        seen.add(key)

        chunk_index = _first_chunk_index_for_offset(offset, chunk_ranges)
        anchors.append(
            {
                "anchor_id": _anchor_id(label),
                "kind": _anchor_kind(match.group("kind")),
                "label": label,
                "page": _page_for_chunk_index(chunk_index, page_chunk_map),
                "chunk_index": chunk_index,
                "caption": stripped,
                "text_preview": stripped[:500],
                "source_span": {
                    "page": _page_for_chunk_index(chunk_index, page_chunk_map),
                    "chunk_index": chunk_index,
                    "paragraph_index": paragraph_index,
                    "line_start": None,
                    "line_end": None,
                },
            }
        )
        offset += len(line)

    return anchors


def _anchor_kind(raw_kind: str) -> str:
    normalized = raw_kind.lower().rstrip(".")
    if normalized == "fig":
        return "figure"
    if normalized == "eq":
        return "equation"
    return normalized


def _anchor_id(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")


def _first_chunk_index_for_offset(offset: int, chunk_ranges: List[tuple]) -> Optional[int]:
    for index, start, end in chunk_ranges:
        if start <= offset < end:
            return index
    return None


def _page_for_chunk_index(
    chunk_index: Optional[int],
    page_chunk_map: List[Dict[str, Any]],
) -> Optional[int]:
    if chunk_index is None:
        return None
    for item in page_chunk_map:
        chunk_indices = item.get("chunk_indices")
        if isinstance(chunk_indices, list) and chunk_index in chunk_indices:
            page = item.get("page")
            return page if isinstance(page, int) else None
    return None


def _chunk_indices_for_range(
    *,
    start: int,
    end: int,
    chunk_ranges: List[tuple],
) -> List[int]:
    return [
        index
        for index, chunk_start, chunk_end in chunk_ranges
        if chunk_start < end and chunk_end > start
    ]


def _format_document_evidence_lines(
    *,
    session_id: str,
    parsed_content_id: Optional[str],
    wrapper_content_id: Optional[str],
    canonical_content_id: Optional[str],
    result,
    cache_error: Optional[str],
    objective: str,
    content_store: ToolContentStore,
) -> List[str]:
    next_actions = _build_document_next_actions(
        canonical_content_id=canonical_content_id or parsed_content_id,
        objective=objective,
    )
    lines = [
        "",
        "[DocumentParse Ranked Preview]",
        f"wrapper_content_id: {wrapper_content_id or ''}",
        f"parsed_content_id: {parsed_content_id or ''}",
        f"canonical_content_id: {canonical_content_id or parsed_content_id or ''}",
        f"objective: {objective}",
        f"ranked_top_k: {_DOCUMENT_EVIDENCE_LIMIT_PER_FILE}",
        "sort_order: descending_relevance_score",
        "ranking_method: fielded_bm25_lexical_chunk_ranking",
        "final_answer_generated: false",
        "scope: first_screen_preview_not_full_evidence_rank",
        "note: rank_preview evidence is already sorted by relevance; item 1 is the highest-ranked preview hit. Call evidence_rank for a full second-pass search.",
        "next_actions: " + json.dumps(next_actions, ensure_ascii=False),
    ]

    if not parsed_content_id:
        lines.extend(
            [
                "rank_preview: {\"evidence\": []}",
                "evidence_coverage: "
                + json.dumps(
                    _build_evidence_coverage(
                        evidence_count=0,
                        result=result,
                    ),
                    ensure_ascii=False,
                ),
                "notes:",
                f"- Parsed document was not cached: {cache_error or 'unknown cache error'}.",
            ]
        )
        return lines

    try:
        ranking_result = rank_evidence(
            query=objective,
            content_ids=[parsed_content_id],
            session_id=session_id,
            max_evidence=_DOCUMENT_EVIDENCE_LIMIT_PER_FILE,
            content_store=content_store,
        )
    except Exception as e:
        log_fail(
            "document_parse evidence extraction",
            repr(e),
            session_id=session_id,
            content_id=parsed_content_id,
        )
        lines.extend(
            [
                "rank_preview: {\"evidence\": []}",
                "evidence_coverage: "
                + json.dumps(
                    _build_evidence_coverage(
                        evidence_count=0,
                        result=result,
                    ),
                    ensure_ascii=False,
                ),
                "notes:",
                "- Document evidence extraction failed; parsed Markdown remains cached.",
            ]
        )
        return lines

    evidence_items = [
        evidence
        for evidence in ranking_result.evidence
        if evidence.content_id == parsed_content_id and evidence.chunk_index >= 0
    ]
    coverage = _build_evidence_coverage(
        evidence_count=len(evidence_items),
        result=result,
    )
    lines.append("evidence_coverage: " + json.dumps(coverage, ensure_ascii=False))

    if not evidence_items:
        lines.append("rank_preview: {\"evidence\": []}")
    else:
        lines.append("rank_preview:")
        lines.append(f"  objective: {objective}")
        lines.append(f"  ranked_top_k: {_DOCUMENT_EVIDENCE_LIMIT_PER_FILE}")
        lines.append("  sort_order: descending_relevance_score")
        lines.append("  ranking_method: fielded_bm25_lexical_chunk_ranking")
        lines.append("  evidence:")
        for index, evidence in enumerate(evidence_items, 1):
            lines.extend(_format_document_evidence_item(index=index, evidence=evidence))

    if ranking_result.notes:
        lines.append("notes:")
        for note in ranking_result.notes:
            lines.append(f"- {note}")

    return lines


def _build_document_next_actions(
    *,
    canonical_content_id: Optional[str],
    objective: str,
) -> List[Dict[str, Any]]:
    if not canonical_content_id:
        return []

    return [
        {
            "action": "rank_evidence",
            "label": "按目标检索更多证据",
            "tool": "evidence_rank",
            "arguments": {
                "content_ids": [canonical_content_id],
                "query": objective,
                "max_evidence": 5,
            },
            "priority": "recommended",
        },
        {
            "action": "read_content",
            "label": "读取解析正文开头",
            "tool": "tool_content_read",
            "arguments": {
                "content_id": canonical_content_id,
                "offset": 0,
            },
            "priority": "optional",
        },
    ]


def _build_evidence_coverage(
    *,
    evidence_count: int,
    result,
) -> Dict[str, Any]:
    if evidence_count >= 2:
        coverage_level = "strong"
    elif evidence_count == 1:
        coverage_level = "partial"
    else:
        coverage_level = "weak"

    missing_aspects: List[str] = []
    if evidence_count == 0:
        missing_aspects.append("No objective-matching chunk surfaced in rank_preview.")
    if result.warnings:
        missing_aspects.append("Parse warnings may affect completeness.")

    return {
        "has_relevant_evidence": evidence_count > 0,
        "coverage_level": coverage_level,
        "missing_aspects": missing_aspects,
    }


def _format_document_evidence_item(
    *,
    index: int,
    evidence: RankedEvidence,
) -> List[str]:
    lines = [
        f"- ranked_order: {index}",
        f"  rank: {index}",
        f"  parsed_content_id: {evidence.content_id}",
        f"  chunk_index: {evidence.chunk_index}",
        "  source_span: "
        + json.dumps(
            {
                "page": None,
                "chunk_index": evidence.chunk_index,
                "paragraph_index": None,
                "line_start": None,
                "line_end": None,
            },
            ensure_ascii=False,
        ),
        f"  title: {evidence.display_title}",
        f"  start_offset: {evidence.start_offset}",
        f"  end_offset: {evidence.end_offset}",
        f"  lexical_score: {evidence.score:.4f}",
        f"  matched_reason: {evidence.matched_reason}",
    ]

    if evidence.term_hit_stats:
        term_hit_stats = []
        for stat in evidence.term_hit_stats:
            term_hit_stats.append(
                {
                    "term": stat.term,
                    "total_count": stat.total_count,
                    "field_stats": [
                        {"field": field_stat.field, "count": field_stat.count}
                        for field_stat in stat.field_stats
                    ],
                }
            )
        lines.append(
            "  term_hit_stats: " + json.dumps(term_hit_stats, ensure_ascii=False)
        )

    if evidence.excerpt:
        lines.append("  excerpt: |-")
        for excerpt_line in evidence.excerpt.splitlines():
            lines.append(f"    {excerpt_line}")

    lines.append("  suggested_next_action: use_document_evidence_or_read_more_context")
    return lines
