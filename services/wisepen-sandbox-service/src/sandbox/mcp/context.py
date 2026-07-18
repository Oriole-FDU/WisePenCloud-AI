"""Tenant context extraction for MCP tool handlers."""
from common.security.context import SecurityContextHolder
from sandbox.gateway.isolation import PathTranslator, TenantScope, PathValidationError


def extract_tenant() -> tuple[str, str]:
    uid = (SecurityContextHolder.get_user_id() or "").strip()
    sid = (SecurityContextHolder.get_session_id() or "").strip()
    return uid, sid


def build_translator(uid: str, sid: str) -> PathTranslator:
    scope = TenantScope(user_id=uid, session_id=sid)
    return PathTranslator(scope)
