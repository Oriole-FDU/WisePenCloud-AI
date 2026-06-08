from typing import Dict, Optional
from urllib.parse import urlparse

from chat.application.tools.browser.services.browser_interact.actions.ref import _SETTLE_WAIT_MS
from chat.application.tools.browser.services.browser_interact.enums import (
    ActionStatus,
    BrowserActionType,
    DiagnosticCode,
    WaitUntil,
)
from chat.application.tools.browser.services.browser_interact.models import (
    ActionResult,
)
from chat.application.tools.browser.services.browser_interact.response.build_response import (
    get_page_state,
)
from chat.application.tools.browser.services.browser_interact.response.error_factory import (
    make_action_failed_error,
    make_schema_error,
    make_user_intervention_error_from_signal,
)
from chat.application.tools.browser.services.browser_interact.runtime.action_runtime import (
    action_error_response,
    action_success_response,
    get_existing_page_or_error,
    get_or_create_page_or_error,
)
from chat.application.tools.browser.services.browser_interact.runtime.content import BrowserContentExtractor
from chat.application.tools.browser.services.browser_interact.runtime.intervention import (
    UserInterventionDetector,
)
from chat.application.tools.browser.services.browser_interact.runtime.session import BrowserSessionManager
from chat.application.tools.browser.services.browser_interact.snapshot.manager import SnapshotManager
from common.logger import log_fail, log_ok

_NAVIGATION_TIMEOUT_MS = 30000


def _redact_url(url: str) -> str:
    """隐藏 URL path/query，避免日志记录敏感参数。"""
    try:
        parsed = urlparse(url)
        if parsed.hostname:
            return f"{parsed.scheme}://{parsed.hostname}/***"
        return url
    except Exception:
        return url


async def handle_navigate(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """导航到目标 URL，必要时创建新的浏览器会话。"""
    url = action.get("url", "")
    if not url:
        return await action_error_response(
            session_manager,
            session_manager.page,
            make_schema_error("navigate action requires a 'url' field."),
        )

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    was_alive = session_manager.is_session_alive

    page, session_error_response = await get_or_create_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    wait_until = action.get("wait_until", WaitUntil.DOM_CONTENT_LOADED.value)
    if wait_until not in {item.value for item in WaitUntil}:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error(
                "wait_until must be domcontentloaded, load, or networkidle."
            ),
        )

    snapshot_manager.invalidate()

    try:
        await page.goto(url, wait_until=wait_until, timeout=_NAVIGATION_TIMEOUT_MS)
        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        error_message = str(error)
        is_timeout = "Timeout" in error_message or "timeout" in error_message
        diagnostic_code = (
            DiagnosticCode.NAVIGATION_TIMEOUT.value
            if is_timeout
            else DiagnosticCode.NAVIGATION_FAILED.value
        )
        log_fail("浏览器导航", error_message, url=_redact_url(url))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.NAVIGATE.value,
                message="Navigation failed.",
                diagnostic_code=diagnostic_code,
            ),
        )

    intervention_error = await intervention.detect(page)
    if intervention_error:
        return await action_error_response(
            session_manager,
            page,
            error=make_user_intervention_error_from_signal(intervention_error),
        )

    page_state = await get_page_state(page)
    log_ok("浏览器导航", url=_redact_url(url))

    created = not was_alive
    reused = was_alive

    return action_success_response(
        session_manager,
        page_state=page_state,
        created=created,
        reused=reused,
        action_result=ActionResult(
            type=BrowserActionType.NAVIGATE.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "url": page.url,
                "title": page_state.title if page_state else "",
                "refs_invalidated": True,
            },
        ),
    )


async def handle_go_back(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """浏览器历史后退。"""
    return await handle_navigation_direction(
        session_manager=session_manager,
        snapshot_manager=snapshot_manager,
        intervention=intervention,
        browser_session_id=browser_session_id,
        action_type=BrowserActionType.GO_BACK,
    )


async def handle_go_forward(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """浏览器历史前进。"""
    return await handle_navigation_direction(
        session_manager=session_manager,
        snapshot_manager=snapshot_manager,
        intervention=intervention,
        browser_session_id=browser_session_id,
        action_type=BrowserActionType.GO_FORWARD,
    )


async def handle_navigation_direction(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    browser_session_id: Optional[str],
    action_type: BrowserActionType,
) -> str:
    """执行历史前进/后退并让现有 refs 失效。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    snapshot_manager.invalidate()

    try:
        if action_type is BrowserActionType.GO_BACK:
            await page.go_back()
        else:
            await page.go_forward()

        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        log_fail("浏览器导航", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=action_type.value,
                message=f"{action_type.value} failed.",
                diagnostic_code=DiagnosticCode.NAVIGATION_FAILED.value,
            ),
        )

    intervention_error = await intervention.detect(page)
    if intervention_error:
        return await action_error_response(
            session_manager,
            page,
            error=make_user_intervention_error_from_signal(intervention_error),
        )

    page_state = await get_page_state(page)
    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=True,
        action_result=ActionResult(
            type=action_type.value,
            status=ActionStatus.COMPLETED.value,
            detail={"url": page.url, "refs_invalidated": True},
        ),
    )
