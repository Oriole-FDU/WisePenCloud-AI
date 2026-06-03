from typing import Dict, Optional

from chat.application.tools.browser.services.browser_interact.enums import (
    ActionStatus,
    BrowserActionType,
)
from chat.application.tools.browser.services.browser_interact.models import (
    ActionResult,
)
from chat.application.tools.browser.services.browser_interact.response.error_factory import make_session_error
from chat.application.tools.browser.services.browser_interact.runtime.action_runtime import (
    action_error_response,
    action_success_response,
    get_page_state,
)
from chat.application.tools.browser.services.browser_interact.runtime.content import BrowserContentExtractor
from chat.application.tools.browser.services.browser_interact.runtime.intervention import (
    UserInterventionDetector,
)
from chat.application.tools.browser.services.browser_interact.runtime.session import BrowserSessionManager
from chat.application.tools.browser.services.browser_interact.snapshot.manager import SnapshotManager


async def handle_status(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """返回当前浏览器会话、页面和 ref 快照状态。"""
    if browser_session_id is not None:
        error_code = await session_manager.validate_session(browser_session_id)
        if error_code is not None:
            return await action_error_response(
                session_manager,
                session_manager.page,
                error=make_session_error(error_code, session_manager.session_id),
            )

    page_state = await get_page_state(session_manager.page)
    dom_version = await session_manager.current_dom_version()

    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=session_manager.is_session_alive,
        action_result=ActionResult(
            type=BrowserActionType.STATUS.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "has_session": session_manager.has_session,
                "is_session_alive": session_manager.is_session_alive,
                "current_snapshot_id": snapshot_manager.current_snapshot_id,
                "dom_version": dom_version,
                "browser_events": session_manager.browser_events_summary(),
            },
        ),
    )


async def handle_clear_browser_events(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """清空 console/pageerror/dialog 观测事件缓冲。"""
    if browser_session_id is not None:
        error_code = await session_manager.validate_session(browser_session_id)
        if error_code is not None:
            return await action_error_response(
                session_manager,
                session_manager.page,
                error=make_session_error(error_code, session_manager.session_id),
            )

    session_manager.clear_browser_events()
    page_state = await get_page_state(session_manager.page)
    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=session_manager.is_session_alive,
        action_result=ActionResult(
            type=BrowserActionType.CLEAR_BROWSER_EVENTS.value,
            status=ActionStatus.COMPLETED.value,
            detail={"browser_events": session_manager.browser_events_summary()},
        ),
    )
