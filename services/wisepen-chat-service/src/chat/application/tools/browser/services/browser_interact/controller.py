import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from chat.application.tools.browser.services.browser_interact.actions.control import (
    handle_key,
    handle_scroll,
    handle_wait,
    handle_wait_for_ref,
    handle_wait_for_text,
)
from chat.application.tools.browser.services.browser_interact.actions.navigation import (
    handle_go_back,
    handle_go_forward,
    handle_navigate,
)
from chat.application.tools.browser.services.browser_interact.actions.observation import (
    handle_get_content,
    handle_screenshot,
    handle_snapshot,
)
from chat.application.tools.browser.services.browser_interact.actions.ref import (
    handle_check_ref,
    handle_click_ref,
    handle_fill_ref,
    handle_select_ref,
)
from chat.application.tools.browser.services.browser_interact.actions.status import (
    handle_clear_browser_events,
    handle_status,
)
from chat.application.tools.browser.services.browser_interact.actions.tabs import (
    handle_close_tab,
    handle_list_tabs,
    handle_new_tab,
    handle_switch_tab,
)
from chat.application.tools.browser.services.browser_interact.enums import (
    BrowserActionType,
    BrowserToolName,
    ControllerLogStage,
    DiagnosticCode,
)
from chat.application.tools.browser.services.browser_interact.models import (
    BrowserLaunchOptions,
    ToolError,
)
from chat.application.tools.browser.services.browser_interact.response.build_response import (
    build_error_response,
    get_page_state,
    get_runtime_state,
    get_session_state,
)
from chat.application.tools.browser.services.browser_interact.response.error_factory import (
    make_internal_error,
    make_no_action_error,
    make_schema_error,
    make_unknown_action_error,
)
from chat.application.tools.browser.services.browser_interact.runtime.content import BrowserContentExtractor
from chat.application.tools.browser.services.browser_interact.runtime.intervention import (
    UserInterventionDetector,
)
from chat.application.tools.browser.services.browser_interact.runtime.session import BrowserSessionManager
from chat.application.tools.browser.services.browser_interact.snapshot.manager import SnapshotManager
from common.logger import log_error, log_event

ActionHandler = Callable[
    [
        BrowserSessionManager,
        SnapshotManager,
        UserInterventionDetector,
        BrowserContentExtractor,
        Optional[str],
        Dict[str, Any],
    ],
    Awaitable[str],
]


ACTION_HANDLERS: Dict[BrowserActionType, ActionHandler] = {
    BrowserActionType.STATUS: handle_status,
    BrowserActionType.CLEAR_BROWSER_EVENTS: handle_clear_browser_events,
    BrowserActionType.NAVIGATE: handle_navigate,
    BrowserActionType.GO_BACK: handle_go_back,
    BrowserActionType.GO_FORWARD: handle_go_forward,
    BrowserActionType.NEW_TAB: handle_new_tab,
    BrowserActionType.LIST_TABS: handle_list_tabs,
    BrowserActionType.SWITCH_TAB: handle_switch_tab,
    BrowserActionType.CLOSE_TAB: handle_close_tab,
    BrowserActionType.SNAPSHOT: handle_snapshot,
    BrowserActionType.SCREENSHOT: handle_screenshot,
    BrowserActionType.CLICK_REF: handle_click_ref,
    BrowserActionType.FILL_REF: handle_fill_ref,
    BrowserActionType.SELECT_REF: handle_select_ref,
    BrowserActionType.CHECK_REF: handle_check_ref,
    BrowserActionType.SCROLL: handle_scroll,
    BrowserActionType.KEY: handle_key,
    BrowserActionType.WAIT: handle_wait,
    BrowserActionType.WAIT_FOR_REF: handle_wait_for_ref,
    BrowserActionType.WAIT_FOR_TEXT: handle_wait_for_text,
    BrowserActionType.GET_CONTENT: handle_get_content,
}


_NEW_SESSION_LOCK_KEY = "__new_browser_session__"


@dataclass(slots=True)
class _SessionLockEntry:
    lock: asyncio.Lock
    ref_count: int = 0


class BrowserInteractController:
    def __init__(
        self,
        launch_options: Optional[BrowserLaunchOptions] = None,
    ) -> None:
        """初始化 browse_interact 协调器。

        Args:
            launch_options: 浏览器启动配置。由 tool/container 注入，避免将本地或沙箱
                运行形态写死在 controller 内部。
        """
        self._session_manager = BrowserSessionManager(
            options=launch_options,
        )
        self._snapshot_manager = SnapshotManager()
        self._intervention = UserInterventionDetector()
        self._content_extractor = BrowserContentExtractor()
        self._session_locks: Dict[str, _SessionLockEntry] = {}
        self._session_locks_guard = asyncio.Lock()

    async def cleanup(self) -> None:
        """释放浏览器会话和 session 级互斥锁。"""
        await self._session_manager.cleanup()
        async with self._session_locks_guard:
            self._session_locks.clear()

    async def execute(
        self,
        request: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """串行执行单个 browser action。

        同一 browser_session_id 的动作使用同一把锁，避免多个 ref action 并发修改
        DOM 状态导致快照失效或点击错位。
        """
        lock_key = self._session_lock_key(request)
        lock_entry = await self._borrow_session_lock(lock_key)
        started = time.monotonic()
        action_type = self._log_action_type(request)
        result = ""
        success = False

        try:
            async with lock_entry.lock:
                result = await self._execute_inner(
                    request=request,
                    context=context,
                )
                success = self._response_succeeded(result)
                return result
        finally:
            await self._release_session_lock(lock_key)
            log_event(
                "tool_perf",
                tool_name=BrowserToolName.BROWSE_INTERACT.value,
                stage=action_type,
                elapsed_ms=int((time.monotonic() - started) * 1000),
                success=success,
                cache_hit=False,
                fallback_used=False,
                worker_count=len(self._session_locks),
            )

    async def _execute_inner(
        self,
        request: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> str:
        """解析请求并分派给具体 action handler。"""
        raw_browser_session_id = request.get("browser_session_id")
        browser_session_id = raw_browser_session_id or None

        action = request.get("action")

        if action is None:
            return await self._no_action()

        if not isinstance(action, dict):
            return await self._invalid_schema("Action must be an object.")

        raw_action_type = action.get("type")

        if not raw_action_type:
            return await self._invalid_schema("Action must have a string 'type' field.")

        try:
            action_type = BrowserActionType(raw_action_type)
        except ValueError:
            return await self._unknown_action(str(raw_action_type))

        handler = ACTION_HANDLERS[action_type]

        try:
            return await handler(
                self._session_manager,
                self._snapshot_manager,
                self._intervention,
                self._content_extractor,
                browser_session_id,
                action,
            )
        except Exception as error:
            log_error(
                "browse_interact 执行",
                str(error),
                action_type=action_type.value,
            )
            return await self._error_response(
                make_internal_error(
                    message="Browse interaction failed unexpectedly.",
                    diagnostic_code=DiagnosticCode.UNHANDLED_CONTROLLER_ERROR.value,
                ),
            )

    async def _no_action(self) -> str:
        """返回缺失 action 的错误响应。"""
        return await self._error_response(make_no_action_error())

    async def _invalid_schema(self, message: str) -> str:
        """返回 action schema 不合法的错误响应。"""
        return await self._error_response(make_schema_error(message))

    async def _unknown_action(self, action_type: str) -> str:
        """返回未知 action type 的错误响应。"""
        return await self._error_response(make_unknown_action_error(action_type))

    async def _error_response(self, error: ToolError) -> str:
        """构建 controller 入口层错误响应。"""
        return build_error_response(
            session_state=get_session_state(
                self._session_manager.session_id,
                valid=self._session_manager.is_session_alive,
            ),
            page_state=await get_page_state(self._session_manager.page),
            runtime_state=get_runtime_state(
                provider=self._session_manager.runtime_provider,
                engine=self._session_manager.runtime_engine,
                sandboxed=self._session_manager.runtime_sandboxed,
                mode=self._session_manager.runtime_mode,
            ),
            error=error,
        )

    def _log_action_type(self, request: Dict[str, Any]) -> str:
        """提取用于性能日志的 action stage。"""
        action = request.get("action")
        if not isinstance(action, dict):
            return ControllerLogStage.INVALID_ACTION.value
        raw_action_type = action.get("type")
        if not isinstance(raw_action_type, str) or not raw_action_type:
            return ControllerLogStage.UNKNOWN_ACTION.value
        return raw_action_type

    def _session_lock_key(self, request: Dict[str, Any]) -> str:
        """根据请求和当前会话生成串行化锁 key。"""
        raw_browser_session_id = request.get("browser_session_id")
        if isinstance(raw_browser_session_id, str) and raw_browser_session_id.strip():
            return raw_browser_session_id.strip()

        current_session_id = self._session_manager.session_id
        if current_session_id:
            return current_session_id

        return _NEW_SESSION_LOCK_KEY

    def _response_succeeded(self, response: str) -> bool:
        """从结构化工具响应中读取 success 标记，用于性能日志。"""
        try:
            payload = json.loads(response)
        except json.JSONDecodeError:
            return False
        return payload.get("success") is True

    async def _borrow_session_lock(self, key: str) -> _SessionLockEntry:
        """获取 session 级锁条目并增加引用计数。"""
        async with self._session_locks_guard:
            entry = self._session_locks.get(key)
            if entry is None:
                entry = _SessionLockEntry(lock=asyncio.Lock())
                self._session_locks[key] = entry
            entry.ref_count += 1
            return entry

    async def _release_session_lock(self, key: str) -> None:
        """释放 session 级锁条目，引用归零后移除。"""
        async with self._session_locks_guard:
            entry = self._session_locks.get(key)
            if entry is None:
                return
            entry.ref_count = max(0, entry.ref_count - 1)
            if entry.ref_count == 0 and not entry.lock.locked():
                self._session_locks.pop(key, None)



