import asyncio
from typing import Dict, List, Optional

from chat.application.tools.browser.services.browser_interact.enums import (
    ActionStatus,
    BrowserActionType,
    DiagnosticCode,
    KeyboardKey,
    ScrollDirection,
    WaitForState,
)
from chat.application.tools.browser.services.browser_interact.models import ActionResult
from chat.application.tools.browser.services.browser_interact.response.error_factory import (
    make_action_failed_error,
    make_schema_error,
    make_user_intervention_error_from_signal,
)
from chat.application.tools.browser.services.browser_interact.runtime.action_runtime import (
    action_error_response,
    action_success_response,
    get_existing_page_or_error,
    get_page_state,
)
from chat.application.tools.browser.services.browser_interact.runtime.content import BrowserContentExtractor
from chat.application.tools.browser.services.browser_interact.runtime.intervention import (
    UserInterventionDetector,
)
from chat.application.tools.browser.services.browser_interact.runtime.session import BrowserSessionManager
from chat.application.tools.browser.services.browser_interact.snapshot.manager import SnapshotManager
from common.logger import log_fail

_SCROLL_STEP_PX = 100
_SETTLE_WAIT_MS = 800
WAIT_DURATION_MAX_S = 30
WAIT_CONDITION_TIMEOUT_MAX_MS = 30000


_KEY_ALIASES = {
    "ctrl": KeyboardKey.CONTROL.value,
    "control": KeyboardKey.CONTROL.value,
    "alt": KeyboardKey.ALT.value,
    "shift": KeyboardKey.SHIFT.value,
    "meta": KeyboardKey.META.value,
    "cmd": KeyboardKey.META.value,
    "command": KeyboardKey.META.value,
    "enter": KeyboardKey.ENTER.value,
    "return": KeyboardKey.ENTER.value,
    "esc": KeyboardKey.ESCAPE.value,
    "escape": KeyboardKey.ESCAPE.value,
    "tab": KeyboardKey.TAB.value,
    "space": KeyboardKey.SPACE.value,
    "backspace": KeyboardKey.BACKSPACE.value,
    "delete": KeyboardKey.DELETE.value,
    "up": KeyboardKey.ARROW_UP.value,
    "down": KeyboardKey.ARROW_DOWN.value,
    "left": KeyboardKey.ARROW_LEFT.value,
    "right": KeyboardKey.ARROW_RIGHT.value,
    "pageup": KeyboardKey.PAGE_UP.value,
    "pagedown": KeyboardKey.PAGE_DOWN.value,
    "home": KeyboardKey.HOME.value,
    "end": KeyboardKey.END.value,
}


def _split_keys(keys_str: str) -> List[str]:
    return [k.strip() for k in keys_str.split("+") if k.strip()]


def _normalize_keys(keys: List[str]) -> List[str]:
    return [_KEY_ALIASES.get(k.lower(), k) for k in keys]


async def handle_scroll(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """按方向滚动页面，并让现有 refs 失效。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    direction = action.get("scroll_direction", ScrollDirection.DOWN.value)
    amount = action.get("scroll_amount", 1)

    amount = max(0, amount)

    scroll_offsets = {
        ScrollDirection.DOWN.value: (0, _SCROLL_STEP_PX * amount),
        ScrollDirection.UP.value: (0, -_SCROLL_STEP_PX * amount),
        ScrollDirection.RIGHT.value: (_SCROLL_STEP_PX * amount, 0),
        ScrollDirection.LEFT.value: (-_SCROLL_STEP_PX * amount, 0),
    }
    if direction not in scroll_offsets:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("scroll_direction must be up, down, left, or right."),
        )
    delta_x, delta_y = scroll_offsets[direction]

    try:
        await page.mouse.wheel(delta_x, delta_y)
        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        log_fail("浏览器滚动", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.SCROLL.value,
                message="Scroll failed.",
                diagnostic_code=DiagnosticCode.SCROLL_FAILED.value,
            ),
        )

    snapshot_manager.invalidate()
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
            type=BrowserActionType.SCROLL.value,
            status=ActionStatus.COMPLETED.value,
            detail={"direction": direction, "amount": amount, "refs_invalidated": True},
        ),
    )


async def handle_key(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """发送键盘按键或组合键，并让现有 refs 失效。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    keys_str = action.get("text", "")

    keys = _normalize_keys(_split_keys(keys_str))

    if not keys:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("key action requires a 'text' field with key combo."),
        )

    try:
        if len(keys) == 1:
            await page.keyboard.press(keys[0])
        else:
            pressed = []
            for key in keys:
                await page.keyboard.down(key)
                pressed.append(key)

            for key in reversed(pressed):
                await page.keyboard.up(key)

        await page.wait_for_timeout(_SETTLE_WAIT_MS)
    except Exception as error:
        if len(keys) > 1:
            for key in reversed(pressed):
                try:
                    await page.keyboard.up(key)
                except Exception:
                    pass

        log_fail("浏览器按键", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.KEY.value,
                message="Key action failed.",
                diagnostic_code=DiagnosticCode.KEY_FAILED.value,
            ),
        )

    snapshot_manager.invalidate()
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
            type=BrowserActionType.KEY.value,
            status=ActionStatus.COMPLETED.value,
            detail={"keys": "+".join(keys), "refs_invalidated": True},
        ),
    )


async def handle_wait(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """等待页面异步状态稳定，并检测是否出现用户介入页面。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    raw_duration = action.get("duration", 1.0)

    if not isinstance(raw_duration, (int, float)) or isinstance(raw_duration, bool):
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("duration must be a number."),
        )
    if raw_duration < 0 or raw_duration > WAIT_DURATION_MAX_S:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error(
                f"duration must be between 0 and {WAIT_DURATION_MAX_S}."
            ),
        )

    duration = raw_duration

    try:
        await page.wait_for_timeout(duration * 1000)
    except Exception as error:
        log_fail("浏览器等待", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.WAIT.value,
                message="Wait failed.",
                diagnostic_code=DiagnosticCode.WAIT_FAILED.value,
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
            type=BrowserActionType.WAIT.value,
            status=ActionStatus.COMPLETED.value,
            detail={"duration_s": duration},
        ),
    )


async def handle_wait_for_ref(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """等待当前 snapshot ref 对应元素达到指定状态。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    ref = action.get("ref")
    if not ref:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("wait_for_ref requires a 'ref' field."),
        )

    state = action.get("state", WaitForState.VISIBLE.value)
    if state not in {item.value for item in WaitForState}:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("wait_for_ref state must be attached, detached, visible, or hidden."),
        )

    timeout_ms = action.get("timeout_ms", 5000)
    if not isinstance(timeout_ms, int) or timeout_ms < 0 or timeout_ms > WAIT_CONDITION_TIMEOUT_MAX_MS:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error(
                f"timeout_ms must be an integer between 0 and {WAIT_CONDITION_TIMEOUT_MAX_MS}."
            ),
        )

    try:
        parsed_ref = ref
        target = await snapshot_manager.resolve_element(page, parsed_ref)
        if state in (WaitForState.DETACHED.value, WaitForState.HIDDEN.value):
            if target is None:
                matched = True
            else:
                matched = not await target.is_visible()
                await target.dispose()
        else:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_ms / 1000
            matched = False
            while loop.time() <= deadline:
                target = await snapshot_manager.resolve_element(page, parsed_ref)
                if target is not None:
                    is_ready = state == WaitForState.ATTACHED.value or await target.is_visible()
                    await target.dispose()
                    if is_ready:
                        matched = True
                        break
                await page.wait_for_timeout(100)
    except Exception as error:
        log_fail("等待 ref", str(error))
        return await action_error_response(
            session_manager,
            page,
            make_action_failed_error(
                action_type=BrowserActionType.WAIT_FOR_REF.value,
                message="wait_for_ref failed.",
                diagnostic_code=DiagnosticCode.WAIT_FAILED.value,
            ),
        )

    if not matched:
        return await action_error_response(
            session_manager,
            page,
            make_action_failed_error(
                action_type=BrowserActionType.WAIT_FOR_REF.value,
                message="wait_for_ref timed out.",
                diagnostic_code=DiagnosticCode.WAIT_CONDITION_TIMEOUT.value,
                retryable=True,
                context={"ref": ref, "state": state, "timeout_ms": timeout_ms},
            ),
        )

    page_state = await get_page_state(page)
    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.WAIT_FOR_REF.value,
            status=ActionStatus.COMPLETED.value,
            detail={"ref": ref, "state": state, "timeout_ms": timeout_ms},
        ),
    )


async def handle_wait_for_text(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """等待页面正文出现或隐藏指定文本。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    text = action.get("text")
    if not isinstance(text, str) or not text:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("wait_for_text requires a non-empty 'text' field."),
        )

    state = action.get("state", WaitForState.VISIBLE.value)
    if state not in (WaitForState.VISIBLE.value, WaitForState.HIDDEN.value):
        return await action_error_response(
            session_manager,
            page,
            make_schema_error("wait_for_text state must be visible or hidden."),
        )

    timeout_ms = action.get("timeout_ms", 5000)
    if not isinstance(timeout_ms, int) or timeout_ms < 0 or timeout_ms > WAIT_CONDITION_TIMEOUT_MAX_MS:
        return await action_error_response(
            session_manager,
            page,
            make_schema_error(
                f"timeout_ms must be an integer between 0 and {WAIT_CONDITION_TIMEOUT_MAX_MS}."
            ),
        )

    try:
        locator = page.get_by_text(text)
        if state == WaitForState.VISIBLE.value:
            await locator.first.wait_for(state=WaitForState.VISIBLE.value, timeout=timeout_ms)
        else:
            await locator.first.wait_for(state=WaitForState.HIDDEN.value, timeout=timeout_ms)
    except Exception:
        return await action_error_response(
            session_manager,
            page,
            make_action_failed_error(
                action_type=BrowserActionType.WAIT_FOR_TEXT.value,
                message="wait_for_text timed out.",
                diagnostic_code=DiagnosticCode.WAIT_CONDITION_TIMEOUT.value,
                retryable=True,
                context={"text": text, "state": state, "timeout_ms": timeout_ms},
            ),
        )

    page_state = await get_page_state(page)
    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.WAIT_FOR_TEXT.value,
            status=ActionStatus.COMPLETED.value,
            detail={"text": text, "state": state, "timeout_ms": timeout_ms},
        ),
    )
