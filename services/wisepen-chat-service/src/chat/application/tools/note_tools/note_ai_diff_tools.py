from __future__ import annotations

import json
from typing import Any, Dict

from common.core.exceptions import RpcError

from chat.application.tools.core import (
    ToolDefinition,
    ToolExecutionError,
    ToolLLMSpec,
    ToolParametersSchema,
    ToolPolicy,
    ToolRiskLevel,
)
from chat.application.tools.note_tools.placeholders import (
    build_note_ai_apply_placeholder,
    build_note_ai_xml_placeholder,
)
from chat.service_client.note_collab_client import (
    NoteAiApplyResponse,
    NoteAiReadResponse,
    NoteCollabClient,
)


NOTE_AI_DIFF_SKILL_ID = "builtin:wisepen-note-ai-diff"
NOTE_AI_DIFF_TOOL_NAMES = frozenset(
    {
        "read_note_aixml",
        "apply_current_note_ai_diff_plan",
    }
)

_DEFAULT_MAX_XML_CHARS = 30000
_MAX_PLAN_OPERATIONS = 200
_MAX_PLAN_JSON_CHARS = 1024 * 1024


def _operation_schema(required: list[str], properties: dict[str, Any]) -> dict[str, Any]:
    common = {
        "opId": {
            "type": "string",
            "minLength": 1,
            "description": "Unique operation id, for example op-1.",
        },
    }
    return {
        "type": "object",
        "properties": {**common, **properties},
        "required": ["opId", *required],
        "additionalProperties": False,
    }


def _kind(value: str) -> dict[str, Any]:
    return {"type": "string", "enum": [value]}


def _target(description: str) -> dict[str, Any]:
    return {"type": "string", "minLength": 1, "description": description}


def _position() -> dict[str, Any]:
    return {"type": "string", "enum": ["before", "after"]}


def _plan_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            "Strict WisePen AI-Diff patch plan. Do not use review_suggestions, "
            "items, before/after, target_id, targetId, type, or op fields. "
            "If application context includes selected_text, treat it as a focus cue; "
            "infer from the user's wording whether it is an exact edit boundary or "
            "only context for a broader edit. "
            "selected_text may span multiple ai_xml targets or blocks; when it does, "
            "split the edit into multiple operations instead of refusing the task. "
            "For replace_text, the text argument replaces the entire ai_xml text target; "
            "when the user explicitly asks to modify only selected_text and it is only "
            "part of that target, preserve the exact prefix and suffix outside selected_text. "
            "Do not keep the original selected_text next to its translation, rewrite, "
            "or correction unless the user explicitly asks to keep it. "
            "Do not include the transformed selected_text more than once; for translation "
            "or rewrite requests, the replacement must be prefix + transformed text + suffix, "
            "with no extra transformed text appended before or after the suffix. "
            "Use formula-specific operations for formulas: replace_inline_math/add_inline_math/"
            "delete_target for <inline-math>, and replace_math_expression/add_block with "
            "blockType='math'/delete_block for <math-expression> formula blocks."
        ),
        "properties": {
            "version": {
                "type": "integer",
                "description": "Plan schema version. Must be 1.",
            },
            "operations": {
                "type": "array",
                "minItems": 1,
                "maxItems": _MAX_PLAN_OPERATIONS,
                "items": {
                    "oneOf": [
                        _operation_schema(
                            ["kind", "target", "text"],
                            {
                                "kind": _kind("replace_text"),
                                "target": _target("Text target id from ai_xml, for example b1:t1."),
                                "text": {
                                    "type": "string",
                                    "description": (
                                        "Full replacement text for this ai_xml text target. "
                                        "When the user explicitly asks to modify only "
                                        "selected_text and selected_text is only a span inside "
                                        "the target, keep all characters before and after that "
                                        "span exactly unchanged. If selected_text spans multiple "
                                        "text targets or blocks, use one replace_text operation per "
                                        "affected text target. Do not include both the original "
                                        "selected span and the transformed span in this value. "
                                        "Do not repeat the transformed span; it must appear exactly "
                                        "once inside the full replacement text unless the user "
                                        "explicitly asks for repetition."
                                    ),
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "target", "text", "href"],
                            {
                                "kind": _kind("replace_link"),
                                "target": _target("Link target id from ai_xml."),
                                "text": {"type": "string"},
                                "href": {
                                    "type": "string",
                                    "description": "Absolute http or https URL.",
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "target", "expression"],
                            {
                                "kind": _kind("replace_inline_math"),
                                "target": _target("Inline math target id from <inline-math id=\"...\"> in ai_xml."),
                                "expression": {
                                    "type": "string",
                                    "description": "Replacement LaTeX expression for the inline math node.",
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "target", "expression"],
                            {
                                "kind": _kind("replace_math_expression"),
                                "target": _target("Math block expression target id from <math-expression id=\"...\"> in ai_xml."),
                                "expression": {
                                    "type": "string",
                                    "description": "Replacement LaTeX expression for the math block.",
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "anchor", "position", "text"],
                            {
                                "kind": _kind("add_text"),
                                "anchor": _target("Text/link/inline math anchor id from ai_xml."),
                                "position": _position(),
                                "text": {"type": "string"},
                            },
                        ),
                        _operation_schema(
                            ["kind", "anchor", "position", "text", "href"],
                            {
                                "kind": _kind("add_link"),
                                "anchor": _target("Text/link/inline math anchor id from ai_xml."),
                                "position": _position(),
                                "text": {"type": "string"},
                                "href": {
                                    "type": "string",
                                    "description": "Absolute http or https URL.",
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "anchor", "position", "expression"],
                            {
                                "kind": _kind("add_inline_math"),
                                "anchor": _target("Text/link/inline math anchor id from ai_xml."),
                                "position": _position(),
                                "expression": {
                                    "type": "string",
                                    "description": "LaTeX expression for the inserted inline math node.",
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "anchor", "position", "blockType"],
                            {
                                "kind": _kind("add_block"),
                                "anchor": _target("Block id from ai_xml, for example b1."),
                                "position": _position(),
                                "blockType": {
                                    "type": "string",
                                    "enum": [
                                        "paragraph",
                                        "heading",
                                        "quote",
                                        "bulletListItem",
                                        "numberedListItem",
                                        "math",
                                    ],
                                },
                                "text": {"type": "string"},
                                "expression": {
                                    "type": "string",
                                    "description": "Required when blockType is math; LaTeX expression for the inserted formula block.",
                                },
                            },
                        ),
                        _operation_schema(
                            ["kind", "target"],
                            {
                                "kind": _kind("delete_target"),
                                "target": _target("Non-block target id from ai_xml."),
                            },
                        ),
                        _operation_schema(
                            ["kind", "target"],
                            {
                                "kind": _kind("delete_block"),
                                "target": _target("Block id from ai_xml."),
                            },
                        ),
                    ]
                },
            },
        },
        "required": ["version", "operations"],
        "additionalProperties": False,
    }


class ReadNoteAixmlTool:
    def __init__(
        self,
        note_collab_client: NoteCollabClient,
        *,
        max_xml_chars: int = _DEFAULT_MAX_XML_CHARS,
        timeout_seconds: float = 8.0,
    ) -> None:
        self._note_collab_client = note_collab_client
        self._max_xml_chars = max_xml_chars
        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["whole_note", "selected_note_scope"],
                    "description": (
                        "Read scope. Use whole_note for the full note, or selected_note_scope "
                        "to read the complete blocks containing the user's current selection. "
                        "The selected text itself is provided separately in application context "
                        "and can be narrower than this block scope or span several blocks. "
                        "selected_note_scope is context for locating one or more targets; infer "
                        "the actual edit scope from the user's wording."
                    ),
                },
            },
            "required": [],
            "additionalProperties": False,
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="read_note_aixml",
                description=(
                    "Read the currently opened WisePen note as lightweight AI XML. "
                    "Call this before proposing AI-Diff edits to the current note. "
                    "When the user selected note text, application context may include "
                    "selected_text and selected_note_scope; prefer selected_note_scope for "
                    "selection-focused edits, and use whole_note when broader context is needed. "
                    "selected_text may be an exact edit boundary or only the user's focus cue; "
                    "it may also span multiple blocks. Infer the intended edit scope from the "
                    "user's wording, and use multiple patch operations when multiple targets are "
                    "affected."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=False,
                persisted_output_placeholder_factory=build_note_ai_xml_placeholder,
                risk_level=ToolRiskLevel.LOW,
                required_context_keys=("active_note_resource_id",),
                timeout_seconds=timeout_seconds,
                max_output_chars=None,
                allow_parallel=False,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> str:
        resource_id = _require_context_resource_id(context)
        scope = self._resolve_scope(context, kwargs.get("scope") or "whole_note")
        actor_user_id = _require_context_user_id(context)

        try:
            result = await self._note_collab_client.read_note_for_ai(
                resource_id=resource_id,
                scope=scope,
                actor_user_id=actor_user_id,
                group_role_map=_context_group_role_map(context),
                require_live_room=True,
                client_state_vector=_context_client_state_vector(context),
                client_content_signature=_context_client_content_signature(context),
            )
        except RpcError as e:
            raise _tool_error_from_rpc(e) from e
        except Exception as e:
            raise ToolExecutionError(
                reason="note_ai_diff_read_failed",
                detail_reason=f"Read current note failed: {type(e).__name__}",
                retryable=True,
                metadata={"detail": str(e)},
            ) from e

        if len(result.ai_xml) > self._max_xml_chars:
            raise ToolExecutionError(
                reason="note_xml_too_large",
                detail_reason=(
                    "The current note XML is too large for this tool result. "
                    "Ask the user to select a smaller scope."
                ),
                retryable=False,
                metadata={"xml_chars": len(result.ai_xml)},
            )

        return _format_read_output(result)

    @staticmethod
    def _resolve_scope(context: dict[str, Any], requested_scope: str) -> dict[str, Any]:
        if requested_scope == "selected_note_scope":
            selected_scope = context.get("active_note_selected_scope")
            if isinstance(selected_scope, dict) and selected_scope:
                return selected_scope
            raise ToolExecutionError(
                reason="selected_note_scope_unavailable",
                detail_reason=(
                    "The application did not provide a selected note scope. Use whole_note instead."
                ),
                retryable=False,
            )
        return {"type": "whole_note"}


class ApplyCurrentNoteAiDiffPlanTool:
    def __init__(
        self,
        note_collab_client: NoteCollabClient,
        *,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._note_collab_client = note_collab_client
        parameters_schema: Dict[str, Any] = {
            "type": "object",
            "properties": {
                "export_handle": {
                    "type": "string",
                    "description": (
                        "The export_handle returned by read_note_aixml in this task."
                    ),
                },
                "plan": {
                    **_plan_schema(),
                },
            },
            "required": ["export_handle", "plan"],
            "additionalProperties": False,
        }
        self._definition = ToolDefinition(
            llm_spec=ToolLLMSpec(
                name="apply_current_note_ai_diff_plan",
                description=(
                    "Apply a WisePen AI-Diff patch plan to the currently opened note. "
                    "The patch is written as review suggestions; it does not directly accept final text. "
                    "If application context includes selected_text, treat it as a focus cue; "
                    "only keep surrounding text unchanged when the user explicitly asks to modify "
                    "only the selection or otherwise preserve the rest. If that exact selection "
                    "spans multiple blocks or targets, use multiple operations; do not reject the "
                    "edit for spanning blocks. In exact-selection replacement, never submit a plan "
                    "that repeats the original selected text or repeats the transformed replacement "
                    "text; each should appear at most once in the affected target."
                ),
                parameters_schema=ToolParametersSchema(parameters_schema),
            ),
            policy=ToolPolicy(
                expose_by_default=False,
                persist_output=False,
                persisted_output_placeholder_factory=build_note_ai_apply_placeholder,
                risk_level=ToolRiskLevel.HIGH,
                required_context_keys=("active_note_resource_id",),
                timeout_seconds=timeout_seconds,
                max_output_chars=None,
                allow_parallel=False,
            ),
        )

    @property
    def definition(self) -> ToolDefinition:
        return self._definition

    async def execute(self, context: dict[str, Any], **kwargs: Any) -> str:
        resource_id = _require_context_resource_id(context)
        actor_user_id = _require_context_user_id(context)
        export_handle = str(kwargs.get("export_handle") or "").strip()
        if not export_handle:
            raise ToolExecutionError(
                reason="missing_export_handle",
                detail_reason="Missing required argument: export_handle.",
                retryable=False,
            )

        plan = kwargs.get("plan")
        _validate_plan_shape(plan)

        try:
            selected_text = _selected_text_for_exact_boundary(context)
            result = await self._note_collab_client.apply_plan_for_ai(
                resource_id=resource_id,
                export_handle=export_handle,
                plan=plan,
                actor_user_id=actor_user_id,
                group_role_map=_context_group_role_map(context),
                selected_text=selected_text,
                selected_text_boundary=True if selected_text else None,
                require_live_room=True,
                client_state_vector=_context_client_state_vector(context),
                client_content_signature=_context_client_content_signature(context),
            )
        except RpcError as e:
            raise _tool_error_from_rpc(e) from e
        except Exception as e:
            raise ToolExecutionError(
                reason="note_ai_diff_apply_failed",
                detail_reason=f"Apply AI-Diff plan failed: {type(e).__name__}",
                retryable=True,
                metadata={"detail": str(e)},
            ) from e

        return _format_apply_output(result)


def _require_context_resource_id(context: dict[str, Any]) -> str:
    resource_id = str(context.get("active_note_resource_id") or "").strip()
    if not resource_id:
        raise ToolExecutionError(
            reason="note_context_unavailable",
            detail_reason="No currently opened note resource is available in tool context.",
            retryable=False,
        )
    return resource_id


def _require_context_user_id(context: dict[str, Any]) -> str:
    user_id = str(context.get("user_id") or "").strip()
    if not user_id:
        raise ToolExecutionError(
            reason="missing_user_context",
            detail_reason="No current user id is available in tool context.",
            retryable=False,
        )
    return user_id


def _context_group_role_map(context: dict[str, Any]) -> dict[str, Any]:
    value = context.get("group_role_map")
    return value if isinstance(value, dict) else {}


def _context_client_state_vector(context: dict[str, Any]) -> str | None:
    value = str(context.get("active_note_client_state_vector") or "").strip()
    return value or None


def _context_client_content_signature(context: dict[str, Any]) -> str | None:
    value = str(context.get("active_note_client_content_signature") or "").strip()
    return value or None


def _selected_text_for_exact_boundary(context: dict[str, Any]) -> str | None:
    selected_text = str(context.get("active_note_selected_text") or "").strip()
    if not selected_text:
        return None
    user_query = str(context.get("active_user_query") or "")
    if not _is_exact_selected_text_request(user_query):
        return None
    return selected_text


def _is_exact_selected_text_request(user_query: str) -> bool:
    text = (user_query or "").strip().lower()
    if not text:
        return False

    additive_markers = (
        "补充",
        "追加",
        "新增",
        "添加",
        "插入",
        "append",
        "add ",
        "insert",
    )
    if any(marker in text for marker in additive_markers):
        return False

    broad_markers = (
        "整段",
        "整个段落",
        "全文",
        "整篇",
        "上下文",
        "周围",
        "前后文",
        "扩写",
        "续写",
        "continue",
        "expand",
    )
    exact_markers = (
        "选中部分",
        "选中的部分",
        "选中文字",
        "选中的文字",
        "选中文本",
        "选中的文本",
        "这部分文字",
        "这部分",
        "这段文字",
        "这句话",
        "当前句",
        "只修改",
        "只需修改",
        "只需要修改",
        "仅修改",
        "其它不变",
        "其他不变",
        "其余不变",
        "保持不变",
        "不要改其他",
        "不要修改其他",
        "selected text",
        "selected part",
        "selection",
        "only selected",
        "翻译",
        "译为",
        "译成",
        "英文",
        "英语",
        "translate",
        "translation",
    )

    has_exact_marker = any(marker in text for marker in exact_markers)
    if not has_exact_marker:
        return False

    if any(marker in text for marker in broad_markers):
        preserve_markers = (
            "只",
            "仅",
            "其它不变",
            "其他不变",
            "其余不变",
            "only",
            "unchanged",
        )
        return any(marker in text for marker in preserve_markers)
    return True


def _validate_plan_shape(plan: Any) -> None:
    if not isinstance(plan, dict):
        raise ToolExecutionError(
            reason="invalid_ai_diff_plan",
            detail_reason="plan must be a JSON object.",
            retryable=False,
        )
    if plan.get("version") != 1 or not isinstance(plan.get("operations"), list):
        raise ToolExecutionError(
            reason="invalid_ai_diff_plan",
            detail_reason="plan must contain version=1 and operations array.",
            retryable=False,
        )
    operations = plan["operations"]
    if len(operations) > _MAX_PLAN_OPERATIONS:
        raise ToolExecutionError(
            reason="invalid_ai_diff_plan",
            detail_reason=f"operations length must be <= {_MAX_PLAN_OPERATIONS}.",
            retryable=False,
        )
    for index, operation in enumerate(operations):
        if not isinstance(operation, dict):
            raise ToolExecutionError(
                reason="invalid_ai_diff_plan",
                detail_reason=f"operation at index {index} must be an object.",
                retryable=False,
            )
        if not str(operation.get("opId") or "").strip():
            raise ToolExecutionError(
                reason="invalid_ai_diff_plan",
                detail_reason=f"operation at index {index} is missing non-empty opId.",
                retryable=False,
            )
        if not str(operation.get("kind") or "").strip():
            raise ToolExecutionError(
                reason="invalid_ai_diff_plan",
                detail_reason=f"operation at index {index} is missing non-empty kind.",
                retryable=False,
            )
    try:
        raw = json.dumps(plan, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        raise ToolExecutionError(
            reason="invalid_ai_diff_plan",
            detail_reason=f"plan must be JSON serializable: {e}",
            retryable=False,
        ) from e
    if len(raw) > _MAX_PLAN_JSON_CHARS:
        raise ToolExecutionError(
            reason="invalid_ai_diff_plan",
            detail_reason=f"serialized plan must be <= {_MAX_PLAN_JSON_CHARS} characters.",
            retryable=False,
        )


def _format_read_output(result: NoteAiReadResponse) -> str:
    lines = [
        "[Note AI XML Read]",
        f"resource_id={result.resource_id}",
        f"export_handle={result.export_handle}",
        f"expires_at={result.expires_at}",
        f"version={result.version}",
        f"scope={json.dumps(result.scope, ensure_ascii=False, separators=(',', ':'))}",
        "",
        "<ai_xml>",
        result.ai_xml.rstrip(),
        "</ai_xml>",
    ]
    if result.skipped_targets:
        lines.extend(
            [
                "",
                "<skipped_targets>",
                json.dumps(result.skipped_targets, ensure_ascii=False, indent=2),
                "</skipped_targets>",
            ]
        )
    return "\n".join(lines)


def _format_apply_output(result: NoteAiApplyResponse) -> str:
    return "\n".join(
        [
            "[Note AI-Diff Apply Result]",
            f"resource_id={result.resource_id}",
            f"export_handle={result.export_handle}",
            f"summary={json.dumps(result.summary.to_node_shape(), ensure_ascii=False, separators=(',', ':'))}",
            f"applied={json.dumps(result.applied, ensure_ascii=False, separators=(',', ':'))}",
            f"stale_applied={json.dumps(result.stale_applied, ensure_ascii=False, separators=(',', ':'))}",
            f"conflicts={json.dumps(result.conflicts, ensure_ascii=False, separators=(',', ':'))}",
            f"skipped={json.dumps(result.skipped, ensure_ascii=False, separators=(',', ':'))}",
        ]
    )


def _tool_error_from_rpc(error: RpcError) -> ToolExecutionError:
    reason = _resolve_rpc_tool_reason(error)
    retryable = reason in {
        "note_collab_timeout",
        "note_collab_rpc_failed",
    }
    return ToolExecutionError(
        reason=reason,
        detail_reason=_resolve_rpc_tool_detail(reason, error.msg or str(error)),
        retryable=retryable,
        metadata={
            "service_name": error.service_name,
            "path": error.path,
            "status": error.status,
            "code": error.code,
        },
    )


def _resolve_rpc_tool_reason(error: RpcError) -> str:
    msg = error.msg or ""
    if msg in _RPC_MSG_TO_TOOL_REASON:
        return _RPC_MSG_TO_TOOL_REASON[msg]
    if "timeout" in msg.lower():
        return "note_collab_timeout"
    return "note_collab_rpc_failed"


def _resolve_rpc_tool_detail(reason: str, fallback: str) -> str:
    if reason == "note_client_state_not_synced":
        return (
            "The note editor has body content updates that the collaboration service "
            "has not received yet. Do not retry read_note_aixml or "
            "apply_current_note_ai_diff_plan in this turn. Ask the user to wait "
            "until the note finishes syncing, then try again."
        )
    return fallback


_RPC_MSG_TO_TOOL_REASON = {
    "not_found": "note_ai_diff_endpoint_unavailable",
    "missing_actor": "missing_user_context",
    "permission_denied": "note_permission_denied",
    "empty_exportable_scope": "empty_exportable_note_scope",
    "export_handle_expired": "export_handle_expired",
    "export_handle_mismatch": "export_handle_mismatch",
    "invalid_plan": "invalid_ai_diff_plan",
    "invalid_request": "invalid_tool_request",
    "invalid_scope": "invalid_tool_request",
    "invalid_scope_range": "invalid_tool_request",
    "scope_block_not_found": "invalid_tool_request",
    "active_room_not_found": "active_note_room_not_found",
    "note_client_state_not_synced": "note_client_state_not_synced",
}
