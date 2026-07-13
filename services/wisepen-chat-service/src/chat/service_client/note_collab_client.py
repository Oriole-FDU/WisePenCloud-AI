from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Mapping

from common.core.exceptions import RpcError
from common.core.constants import SecurityConstants
from common.http.rpc_client import RpcClient


_DEFAULT_SERVICE_NAME = "wisepen-note-collab-service"
_READ_NOTE_PATH = "/internal/ai-note/read"
_APPLY_PLAN_PATH = "/internal/ai-note/apply-plan"


@dataclass(frozen=True)
class NoteAiReadResponse:
    resource_id: str
    export_handle: str
    scope: dict[str, Any]
    version: int
    ai_xml: str
    skipped_targets: list[dict[str, Any]] = field(default_factory=list)
    expires_at: str = ""

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "NoteAiReadResponse":
        return cls(
            resource_id=str(payload.get("resourceId") or ""),
            export_handle=str(payload.get("exportHandle") or ""),
            scope=dict(payload.get("scope") or {}),
            version=int(payload.get("version") or 0),
            ai_xml=str(payload.get("aiXml") or ""),
            skipped_targets=[
                dict(item)
                for item in (payload.get("skippedTargets") or [])
                if isinstance(item, Mapping)
            ],
            expires_at=str(payload.get("expiresAt") or ""),
        )


@dataclass(frozen=True)
class NoteAiApplySummary:
    applied: int = 0
    stale_applied: int = 0
    conflicts: int = 0
    skipped: int = 0

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "NoteAiApplySummary":
        return cls(
            applied=int(payload.get("applied") or 0),
            stale_applied=int(payload.get("staleApplied") or 0),
            conflicts=int(payload.get("conflicts") or 0),
            skipped=int(payload.get("skipped") or 0),
        )

    def to_node_shape(self) -> dict[str, int]:
        return {
            "applied": self.applied,
            "staleApplied": self.stale_applied,
            "conflicts": self.conflicts,
            "skipped": self.skipped,
        }


@dataclass(frozen=True)
class NoteAiApplyResponse:
    resource_id: str
    export_handle: str
    summary: NoteAiApplySummary
    applied: list[str] = field(default_factory=list)
    stale_applied: list[str] = field(default_factory=list)
    conflicts: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_response(cls, payload: Mapping[str, Any]) -> "NoteAiApplyResponse":
        summary_payload = payload.get("summary") or {}
        if not isinstance(summary_payload, Mapping):
            summary_payload = {}
        return cls(
            resource_id=str(payload.get("resourceId") or ""),
            export_handle=str(payload.get("exportHandle") or ""),
            summary=NoteAiApplySummary.from_response(summary_payload),
            applied=[str(item) for item in (payload.get("applied") or [])],
            stale_applied=[str(item) for item in (payload.get("staleApplied") or [])],
            conflicts=[
                dict(item)
                for item in (payload.get("conflicts") or [])
                if isinstance(item, Mapping)
            ],
            skipped=[
                dict(item)
                for item in (payload.get("skipped") or [])
                if isinstance(item, Mapping)
            ],
        )


class NoteCollabClient:
    def __init__(
        self,
        rpc: RpcClient,
        *,
        service_name: str = _DEFAULT_SERVICE_NAME,
        gateway_base_url: str | None = None,
        read_timeout_seconds: float = 8.0,
        apply_timeout_seconds: float = 10.0,
    ) -> None:
        self._rpc = rpc
        self._service_name = service_name
        self._gateway_base_url = (gateway_base_url or "").strip().rstrip("/") or None
        self._read_timeout_seconds = read_timeout_seconds
        self._apply_timeout_seconds = apply_timeout_seconds

    async def read_note_for_ai(
        self,
        *,
        resource_id: str,
        scope: dict[str, Any] | None = None,
        actor_user_id: str | None = None,
        group_role_map: Mapping[str, Any] | None = None,
        require_live_room: bool = False,
        client_state_vector: str | None = None,
        client_content_signature: str | None = None,
    ) -> NoteAiReadResponse:
        resource_id = (resource_id or "").strip()
        json_payload: dict[str, Any] = {
            "resourceId": resource_id,
            "scope": scope or {"type": "whole_note"},
            "requireLiveRoom": bool(require_live_room),
        }
        client_state_vector = (client_state_vector or "").strip()
        if client_state_vector:
            json_payload["clientStateVector"] = client_state_vector
        client_content_signature = (client_content_signature or "").strip()
        if client_content_signature:
            json_payload["clientContentSignature"] = client_content_signature
        data = await self._rpc.post(
            self._service_name,
            _READ_NOTE_PATH,
            json=json_payload,
            params={"resourceId": resource_id},
            headers=_build_actor_headers(
                actor_user_id=actor_user_id,
                group_role_map=group_role_map,
            ),
            base_url=self._gateway_base_url,
            affinity_key=resource_id,
            timeout=self._read_timeout_seconds,
        )
        if not isinstance(data, Mapping):
            raise RpcError(
                service_name=self._service_name,
                path=_READ_NOTE_PATH,
                msg=f"unexpected data payload: {data!r}",
            )
        return NoteAiReadResponse.from_response(data)

    async def apply_plan_for_ai(
        self,
        *,
        resource_id: str,
        export_handle: str,
        plan: dict[str, Any],
        actor_user_id: str | None = None,
        group_role_map: Mapping[str, Any] | None = None,
        selected_text: str | None = None,
        selected_text_boundary: bool | None = None,
        require_live_room: bool = False,
        client_state_vector: str | None = None,
        client_content_signature: str | None = None,
    ) -> NoteAiApplyResponse:
        resource_id = (resource_id or "").strip()
        json_payload: dict[str, Any] = {
            "resourceId": resource_id,
            "exportHandle": export_handle,
            "plan": plan,
            "requireLiveRoom": bool(require_live_room),
        }
        selected_text = (selected_text or "").strip()
        if selected_text:
            json_payload["selectedText"] = selected_text
            json_payload["selectedTextBoundary"] = bool(selected_text_boundary)
        client_state_vector = (client_state_vector or "").strip()
        if client_state_vector:
            json_payload["clientStateVector"] = client_state_vector
        client_content_signature = (client_content_signature or "").strip()
        if client_content_signature:
            json_payload["clientContentSignature"] = client_content_signature
        data = await self._rpc.post(
            self._service_name,
            _APPLY_PLAN_PATH,
            json=json_payload,
            params={"resourceId": resource_id},
            headers=_build_actor_headers(
                actor_user_id=actor_user_id,
                group_role_map=group_role_map,
            ),
            base_url=self._gateway_base_url,
            affinity_key=resource_id,
            timeout=self._apply_timeout_seconds,
        )
        if not isinstance(data, Mapping):
            raise RpcError(
                service_name=self._service_name,
                path=_APPLY_PLAN_PATH,
                msg=f"unexpected data payload: {data!r}",
        )
        return NoteAiApplyResponse.from_response(data)


def _build_actor_headers(
    *,
    actor_user_id: str | None,
    group_role_map: Mapping[str, Any] | None = None,
) -> dict[str, str] | None:
    user_id = str(actor_user_id or "").strip()
    if not user_id:
        return None

    headers = {
        SecurityConstants.HEADER_USER_ID: user_id,
    }
    if group_role_map is not None:
        headers[SecurityConstants.HEADER_GROUP_ROLE_MAP] = _serialize_group_role_map(
            group_role_map
        )
    return headers


def _serialize_group_role_map(group_role_map: Mapping[str, Any]) -> str:
    serialized: dict[str, int] = {}
    for group_id, role in (group_role_map or {}).items():
        try:
            serialized[str(group_id)] = int(role.code)
        except AttributeError:
            try:
                serialized[str(group_id)] = int(role)
            except (TypeError, ValueError):
                continue
        except (TypeError, ValueError):
            continue
    return json.dumps(serialized, ensure_ascii=False, separators=(",", ":"))
