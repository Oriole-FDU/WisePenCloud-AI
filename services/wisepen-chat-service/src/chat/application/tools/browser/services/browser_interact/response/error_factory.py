from typing import Any, Dict, Optional

from chat.application.tools.browser.services.browser_interact.enums import (
    DiagnosticCode,
    ErrorCategory,
    InterventionSignalType,
    RecoveryHintType,
    ToolErrorCode,
    UserActionType,
)
from chat.application.tools.browser.services.browser_interact.models import (
    InterventionSignal,
    RecoveryHint,
    ToolError,
    UserActionRequest,
)


def make_no_action_error() -> ToolError:
    """构建缺少 action 的 schema 错误。"""
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.NO_ACTION.value,
        message="No action provided. Must specify an action with a 'type' field.",
        retryable=False,
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.FIX_REQUEST.value,
            required_before_retry=True,
            reason="Provide an action object with a valid type field.",
        ),
    )


def make_schema_error(
    message: str,
    *,
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    """构建 action payload 字段不合法的错误。"""
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.INVALID_ACTION_SCHEMA.value,
        message=message,
        retryable=False,
        context=context or {},
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.FIX_REQUEST.value,
            required_before_retry=True,
            reason="Fix the action payload before retrying.",
        ),
    )


def make_unknown_action_error(action_type: str) -> ToolError:
    """构建未知 action type 错误。"""
    return ToolError(
        category=ErrorCategory.INVALID_REQUEST.value,
        code=ToolErrorCode.UNKNOWN_ACTION.value,
        message=f"Unknown action type: '{action_type}'",
        retryable=False,
        context={"action_type": action_type},
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.FIX_REQUEST.value,
            required_before_retry=True,
            reason="Use one of the supported browse_interact action types.",
        ),
    )


def make_session_error(
    error_code: ToolErrorCode,
    current_session_id: Optional[str] = None,
) -> ToolError:
    """根据会话状态错误码构建结构化错误。"""
    if error_code is ToolErrorCode.SESSION_MISMATCH:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_MISMATCH.value,
            message="browser_session_id mismatch.",
            retryable=True,
            context={"current_session_id": current_session_id},
            recovery_hint=RecoveryHint(
                type=RecoveryHintType.REFRESH_STATUS.value,
                required_before_retry=True,
                reason="Recover the current browser_session_id before retrying session-bound actions.",
            ),
        )

    if error_code is ToolErrorCode.SESSION_EXPIRED:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_EXPIRED.value,
            message="Browser page has been closed. The session is expired.",
            retryable=True,
            recovery_hint=RecoveryHint(
                type=RecoveryHintType.RESTART_SESSION.value,
                required_before_retry=True,
                reason="A new browser session must be created before further page actions.",
            ),
        )

    if error_code is ToolErrorCode.SESSION_REQUIRED:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_REQUIRED.value,
            message="browser_session_id is required for this action.",
            retryable=True,
            context={"current_session_id": current_session_id},
            recovery_hint=RecoveryHint(
                type=(
                    RecoveryHintType.REFRESH_STATUS.value
                    if current_session_id
                    else RecoveryHintType.START_SESSION.value
                ),
                required_before_retry=True,
                reason=(
                    "Recover current session state before retrying."
                    if current_session_id
                    else "Start a new browser session before retrying."
                ),
            ),
        )

    if error_code is ToolErrorCode.SESSION_NOT_FOUND:
        return ToolError(
            category=ErrorCategory.SESSION.value,
            code=ToolErrorCode.SESSION_NOT_FOUND.value,
            message="No active browser session matches the provided browser_session_id.",
            retryable=True,
            recovery_hint=RecoveryHint(
                type=RecoveryHintType.START_SESSION.value,
                required_before_retry=True,
                reason="A new session must be started before continuing.",
            ),
        )

    return make_internal_error(
        message=f"Unexpected session error: {error_code}",
        diagnostic_code=DiagnosticCode.UNEXPECTED_SESSION_ERROR.value,
    )


def make_snapshot_required_error() -> ToolError:
    """构建 ref 操作前缺少有效快照的错误。"""
    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.SNAPSHOT_REQUIRED.value,
        message="A current snapshot is required before using refs.",
        retryable=True,
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.REFRESH_SNAPSHOT.value,
            required_before_retry=True,
            reason="The previous action invalidated refs. Take a fresh snapshot before using click_ref/fill_ref/select_ref/check_ref.",
        ),
    )


def make_stale_ref_error(snapshot_id: str) -> ToolError:
    """构建调用方传入过期 snapshot_id 的错误。"""
    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.STALE_REF.value,
        message="The provided snapshot_id is stale.",
        retryable=True,
        context={"provided_snapshot_id": snapshot_id},
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.REFRESH_SNAPSHOT.value,
            required_before_retry=True,
            reason="A fresh snapshot is required before retrying ref-based actions.",
        ),
    )


def make_ref_not_found_error(
    ref: str,
    snapshot_id: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    """构建 ref 无法在当前快照中定位的错误。"""
    error_context: Dict[str, Any] = {"ref": ref}
    if snapshot_id:
        error_context["snapshot_id"] = snapshot_id
    error_context.update(context or {})

    return ToolError(
        category=ErrorCategory.SNAPSHOT.value,
        code=ToolErrorCode.REF_NOT_FOUND.value,
        message="The provided ref was not found in the current page.",
        retryable=True,
        context=error_context,
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.REFRESH_SNAPSHOT.value,
            required_before_retry=True,
            reason="A fresh snapshot is required before retrying ref-based actions.",
        ),
    )


def make_action_failed_error(
    *,
    action_type: str,
    message: str,
    diagnostic_code: str,
    retryable: bool = False,
    context: Optional[Dict[str, Any]] = None,
    recovery_hint: Optional[RecoveryHint] = None,
) -> ToolError:
    """构建 action 执行失败错误。"""
    return ToolError(
        category=ErrorCategory.ACTION.value,
        code=ToolErrorCode.ACTION_FAILED.value,
        message=message,
        retryable=retryable,
        diagnostic_code=diagnostic_code,
        context={
            "action_type": action_type,
            **(context or {}),
        },
        recovery_hint=recovery_hint
        or RecoveryHint(
            type=RecoveryHintType.INSPECT_PAGE_STATE.value,
            required_before_retry=False,
            reason=(
                "Inspect current page state before deciding whether to retry, "
                "refresh snapshot, or change strategy."
            ),
        ),
    )


def make_user_intervention_error_from_signal(signal: InterventionSignal) -> ToolError:
    """将页面介入信号转换为工具错误。"""
    if signal.type == InterventionSignalType.AUTH_PAGE.value:
        user_action_type = UserActionType.LOGIN_OR_VERIFICATION.value
        user_action_message = (
            "请在打开的浏览器窗口中完成登录或验证，然后让 agent 继续。"
        )
        message = (
            "Authentication or verification is required in the visible browser window."
        )
    elif signal.type == InterventionSignalType.CAPTCHA.value:
        user_action_type = UserActionType.CAPTCHA.value
        user_action_message = "请在打开的浏览器窗口中完成验证码，然后让 agent 继续。"
        message = "CAPTCHA detected on the page. User intervention required."
    elif signal.type == InterventionSignalType.HIGH_RISK_ACTION.value:
        user_action_type = UserActionType.CONFIRM_HIGH_RISK_ACTION.value
        user_action_message = (
            "请确认该高风险页面动作是否应继续；如需继续，请在可见浏览器窗口中手动完成该动作。"
        )
        message = "High-risk page action requires explicit user intervention."
    elif signal.type == InterventionSignalType.SECRET_INPUT.value:
        user_action_type = UserActionType.ENTER_SECRET_MANUALLY.value
        user_action_message = (
            "请在打开的浏览器窗口中手动输入密码、验证码或其他敏感凭据，然后让 agent 继续。"
        )
        message = "Sensitive credential input must be completed by the user."
    else:
        user_action_type = signal.type
        user_action_message = signal.reason
        message = signal.reason

    return ToolError(
        category=ErrorCategory.USER_INTERVENTION.value,
        code=ToolErrorCode.USER_INTERVENTION_REQUIRED.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        user_action=UserActionRequest(
            type=user_action_type,
            message=user_action_message,
            detected_reason=signal.type,
        ),
        context={
            "detected_reason": signal.type,
        },
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.WAIT_FOR_USER.value,
            required_before_retry=True,
            reason=(
                "User must complete the requested action in the visible browser "
                "window before the agent continues."
            ),
        ),
    )


def make_browser_unavailable_error(
    *,
    message: str,
    diagnostic_code: str,
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    """构建浏览器运行时不可用错误。"""
    return ToolError(
        category=ErrorCategory.BROWSER_UNAVAILABLE.value,
        code=ToolErrorCode.BROWSER_UNAVAILABLE.value,
        message=message,
        retryable=True,
        requires_user_action=True,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.RESTART_BROWSER_RUNTIME.value,
            required_before_retry=True,
            reason="The browser runtime must be restored before continuing.",
        ),
    )


def make_internal_error(
    *,
    message: str,
    diagnostic_code: str = DiagnosticCode.INTERNAL_ERROR.value,
    context: Optional[Dict[str, Any]] = None,
) -> ToolError:
    """构建未分类内部错误。"""
    return ToolError(
        category=ErrorCategory.INTERNAL.value,
        code=ToolErrorCode.INTERNAL_ERROR.value,
        message=message,
        retryable=False,
        diagnostic_code=diagnostic_code,
        context=context or {},
        recovery_hint=RecoveryHint(
            type=RecoveryHintType.INSPECT_STATUS.value,
            required_before_retry=False,
            reason="Inspect current tool state before deciding whether to retry.",
        ),
    )
