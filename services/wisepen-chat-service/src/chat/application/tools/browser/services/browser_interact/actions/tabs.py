from typing import Dict, Optional

from chat.application.tools.browser.services.browser_interact.enums import (
    ActionStatus,
    BrowserActionType,
    DiagnosticCode,
)
from chat.application.tools.browser.services.browser_interact.models import ActionResult
from chat.application.tools.browser.services.browser_interact.response.build_response import (
    get_page_state,
)
from chat.application.tools.browser.services.browser_interact.response.error_factory import (
    make_action_failed_error,
    make_schema_error,
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


async def handle_list_tabs(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """列出当前 browser context 内所有打开的 tab。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    tabs = await session_manager.list_tabs()
    return action_success_response(
        session_manager,
        page_state=await get_page_state(page),
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.LIST_TABS.value,
            status=ActionStatus.COMPLETED.value,
            detail={"tabs": tabs, "tab_count": len(tabs)},
        ),
    )


async def handle_new_tab(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """创建新 tab，并在提供 url 时导航到目标页面。"""
    page, session_error_response = await get_or_create_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    url = action.get("url")
    if url and not isinstance(url, str):
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("new_tab url must be a string when provided."),
        )

    if isinstance(url, str) and url and not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        page = await session_manager.new_tab(url)
    except Exception as error:
        return await action_error_response(
            session_manager,
            session_manager.page,
            make_action_failed_error(
                action_type=BrowserActionType.NEW_TAB.value,
                message=f"New tab failed: {error}",
                diagnostic_code=DiagnosticCode.TAB_OPERATION_FAILED.value,
            ),
        )

    snapshot_manager.invalidate()
    tabs = await session_manager.list_tabs()
    return action_success_response(
        session_manager,
        page_state=await get_page_state(page),
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.NEW_TAB.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "tabs": tabs,
                "tab_count": len(tabs),
                "refs_invalidated": True,
            },
        ),
    )


async def handle_switch_tab(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """切换当前活动 tab。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    tab_index = action.get("tab_index")
    if not isinstance(tab_index, int):
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("switch_tab requires an integer 'tab_index'."),
        )

    next_page = await session_manager.switch_tab(tab_index)
    if next_page is None:
        return await action_error_response(
            session_manager,
            page,
            make_action_failed_error(
                action_type=BrowserActionType.SWITCH_TAB.value,
                message="Requested tab does not exist or is closed.",
                diagnostic_code=DiagnosticCode.TAB_OPERATION_FAILED.value,
                context={"tab_index": tab_index},
            ),
        )

    snapshot_manager.invalidate()
    return action_success_response(
        session_manager,
        page_state=await get_page_state(next_page),
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.SWITCH_TAB.value,
            status=ActionStatus.COMPLETED.value,
            detail={"tab_index": tab_index, "refs_invalidated": True},
        ),
    )


async def handle_close_tab(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """关闭指定 tab，默认关闭当前 tab。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    tab_index = action.get("tab_index")
    if tab_index is not None and not isinstance(tab_index, int):
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("close_tab tab_index must be an integer when provided."),
        )

    next_page = await session_manager.close_tab(tab_index)
    snapshot_manager.invalidate()
    tabs = await session_manager.list_tabs()
    return action_success_response(
        session_manager,
        page_state=await get_page_state(next_page),
        reused=session_manager.is_session_alive,
        action_result=ActionResult(
            type=BrowserActionType.CLOSE_TAB.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "closed_tab_index": tab_index,
                "tabs": tabs,
                "tab_count": len(tabs),
                "refs_invalidated": True,
            },
        ),
    )
