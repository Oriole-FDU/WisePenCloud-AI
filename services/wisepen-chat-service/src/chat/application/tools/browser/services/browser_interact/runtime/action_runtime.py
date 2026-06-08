from typing import Optional, Tuple

from playwright.async_api import Page

from chat.application.tools.browser.services.browser_interact.enums import (
    DiagnosticCode,
    ToolErrorCode,
)
from chat.application.tools.browser.services.browser_interact.errors import BrowserSessionError
from chat.application.tools.browser.services.browser_interact.models import (
    ActionResult,
    PageState,
    SnapshotPayload,
    ToolError,
)
from chat.application.tools.browser.services.browser_interact.response.build_response import (
    build_error_response,
    build_success_response,
    get_page_state,
    get_runtime_state,
    get_session_state,
)
from chat.application.tools.browser.services.browser_interact.response.error_factory import (
    make_browser_unavailable_error,
    make_schema_error,
    make_session_error,
)
from chat.application.tools.browser.services.browser_interact.runtime.session import BrowserSessionManager
from chat.application.tools.browser.services.browser_interact.snapshot.manager import parse_ref


def session_state(
    session_manager: BrowserSessionManager,
    *,
    created: bool = False,
    reused: bool = False,
):
    """按当前 BrowserSessionManager 构建响应会话状态。"""
    return get_session_state(
        session_manager.session_id,
        valid=session_manager.is_session_alive,
        created=created,
        reused=reused,
    )


def runtime_state(session_manager: BrowserSessionManager):
    """按当前 BrowserSessionManager 构建响应运行时状态。"""
    return get_runtime_state(
        provider=session_manager.runtime_provider,
        engine=session_manager.runtime_engine,
        sandboxed=session_manager.runtime_sandboxed,
        mode=session_manager.runtime_mode,
    )


async def action_error_response(
    session_manager: BrowserSessionManager,
    page: Optional[Page],
    error: ToolError,
) -> str:
    """构建携带当前运行态和页面态的 action 错误响应。"""
    return build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(page),
        error=error,
        runtime_state=runtime_state(session_manager),
    )


def action_success_response(
    session_manager: BrowserSessionManager,
    *,
    page_state: Optional[PageState],
    action_result: Optional[ActionResult] = None,
    snapshot: Optional[SnapshotPayload] = None,
    screenshot: Optional[str] = None,
    created: bool = False,
    reused: bool = False,
) -> str:
    """构建携带当前运行态和页面态的 action 成功响应。"""
    return build_success_response(
        session_state=session_state(session_manager, created=created, reused=reused),
        page_state=page_state,
        runtime_state=runtime_state(session_manager),
        action_result=action_result,
        snapshot=snapshot,
        screenshot=screenshot,
    )


async def get_existing_page_or_error(
    session_manager: BrowserSessionManager,
    browser_session_id: Optional[str],
) -> Tuple[Optional[Page], Optional[str]]:
    """校验会话并返回现有页面，不创建新浏览器。"""
    page, error_code = await session_manager.get_existing_page(browser_session_id)

    if error_code is None:
        return page, None

    return None, build_error_response(
        session_state=session_state(session_manager),
        page_state=await get_page_state(session_manager.page),
        error=make_session_error(error_code, session_manager.session_id),
        runtime_state=runtime_state(session_manager),
    )


async def selector_or_error_response(
    session_manager: BrowserSessionManager,
    page: Page,
    ref: str,
) -> Tuple[Optional[str], Optional[str]]:
    """解析 snapshot ref，失败时返回 schema 错误响应。"""
    parsed = parse_ref(ref)
    if parsed is None:
        return None, await action_error_response(
            session_manager,
            page,
            make_schema_error(
                f"Invalid ref '{ref}'. Ref must be an exact id from the latest snapshot, such as 'e1', 'e2', 'e123'. "
                f"Do NOT use role names like 'searchbox', 'button', 'link', 'textbox' or labels like 'Search'."
            ),
        )
    return parsed, None


async def get_or_create_page_or_error(
    session_manager: BrowserSessionManager,
    browser_session_id: Optional[str],
) -> Tuple[Optional[Page], Optional[str]]:
    """获取已有页面或创建新浏览器页面，并把会话错误转为响应。"""
    try:
        page, error_code = await session_manager.get_or_create_page(browser_session_id)
    except BrowserSessionError as error:
        return None, build_error_response(
            session_state=session_state(session_manager),
            page_state=await get_page_state(session_manager.page),
            error=browser_error_from_diagnostic(error.diagnostic_code),
            runtime_state=runtime_state(session_manager),
        )

    if error_code is not None:
        return None, build_error_response(
            session_state=session_state(session_manager),
            page_state=await get_page_state(session_manager.page),
            error=browser_error_from_code(error_code, session_manager.session_id),
            runtime_state=runtime_state(session_manager),
        )

    return page, None


def browser_error_from_diagnostic(diagnostic_code: str) -> ToolError:
    """将浏览器启动诊断码映射为外部错误。"""
    if diagnostic_code == DiagnosticCode.NO_DISPLAY_SERVER.value:
        return make_browser_unavailable_error(
            message="No display server available for headed browser.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == DiagnosticCode.BROWSER_CRASHED_ON_START.value:
        return make_browser_unavailable_error(
            message="Browser process crashed immediately after launch.",
            diagnostic_code=diagnostic_code,
        )
    if diagnostic_code == DiagnosticCode.LAUNCH_TIMEOUT.value:
        return make_browser_unavailable_error(
            message="Browser launch timed out.",
            diagnostic_code=diagnostic_code,
        )
    return make_browser_unavailable_error(
        message="Browser runtime is unavailable.",
        diagnostic_code=diagnostic_code,
    )


def browser_error_from_code(
    error_code: ToolErrorCode,
    current_session_id: Optional[str],
) -> ToolError:
    """将会话错误码映射为外部错误。"""
    return make_session_error(error_code, current_session_id)
