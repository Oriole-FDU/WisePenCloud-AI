import json
from dataclasses import asdict
from typing import Any, Dict, Optional

from playwright.async_api import Page

from chat.application.tools.browser.services.browser_interact.models import (
    ActionResult,
    BrowserRuntimeState,
    PageState,
    SessionState,
    SnapshotPayload,
    ToolError,
)
from common.logger import log_fail


def build_success_response(
    *,
    session_state: SessionState,
    page_state: Optional[PageState],
    runtime_state: Optional[BrowserRuntimeState] = None,
    action_result: Optional[ActionResult] = None,
    snapshot: Optional[SnapshotPayload] = None,
    screenshot: Optional[str] = None,
) -> str:
    """构建 browse_interact 成功响应 JSON。

    Args:
        session_state: 当前浏览器会话状态。
        page_state: 当前页面状态；无页面时为 None。
        runtime_state: 浏览器运行环境状态。
        action_result: 当前 action 的执行摘要。
        snapshot: snapshot action 生成的快照载荷。
        screenshot: screenshot action 生成的 base64 图片。

    Returns:
        str: 可直接返回给 LLM tool call 的 JSON 字符串。
    """
    payload: Dict[str, Any] = {
        "success": True,
        "browser_session_id": session_state.browser_session_id,
        "session": asdict(session_state),
        "page": asdict(page_state) if page_state else None,
    }

    if runtime_state is not None:
        payload["runtime"] = asdict(runtime_state)

    if action_result is not None:
        payload["action_result"] = asdict(action_result)

    if snapshot is not None:
        payload["snapshot"] = asdict(snapshot)

    if screenshot is not None:
        payload["screenshot"] = screenshot

    return json.dumps(payload, ensure_ascii=False)


def build_error_response(
    *,
    session_state: SessionState,
    page_state: Optional[PageState],
    error: ToolError,
    runtime_state: Optional[BrowserRuntimeState] = None,
) -> str:
    """构建 browse_interact 失败响应 JSON。

    Args:
        session_state: 当前浏览器会话状态。
        page_state: 当前页面状态；无页面时为 None。
        error: 结构化错误。
        runtime_state: 浏览器运行环境状态。

    Returns:
        str: 可直接返回给 LLM tool call 的 JSON 字符串。
    """
    payload: Dict[str, Any] = {
        "success": False,
        "browser_session_id": session_state.browser_session_id,
        "session": asdict(session_state),
        "page": asdict(page_state) if page_state else None,
        "error": error_to_dict(error),
    }

    if runtime_state is not None:
        payload["runtime"] = asdict(runtime_state)

    return json.dumps(payload, ensure_ascii=False)


def error_to_dict(error: ToolError) -> Dict[str, Any]:
    """将 ToolError dataclass 转成响应中的稳定字典结构。"""
    payload: Dict[str, Any] = {
        "category": error.category,
        "code": error.code,
        "message": error.message,
        "retryable": error.retryable,
        "requires_user_action": error.requires_user_action,
    }

    if error.user_action is not None:
        payload["user_action"] = asdict(error.user_action)

    if error.recovery_hint is not None:
        payload["recovery_hint"] = asdict(error.recovery_hint)

    if error.context:
        payload["context"] = error.context

    if error.diagnostic_code is not None:
        payload["diagnostic_code"] = error.diagnostic_code

    return payload


async def get_page_state(page: Optional[Page]) -> Optional[PageState]:
    """读取当前 Page 的轻量状态。

    Args:
        page: Playwright 页面对象；没有会话或页面已释放时可能为 None。

    Returns:
        Optional[PageState]: 读取成功或兜底后的页面状态。
    """
    if page is None:
        return None

    try:
        if page.is_closed():
            return PageState(
                url="",
                title="",
                ready_state=None,
                is_closed=True,
            )

        url = page.url
        title = await page.title()
        ready_state = await page.evaluate("() => document.readyState")

        return PageState(
            url=url,
            title=title,
            ready_state=ready_state,
            is_closed=False,
        )

    except Exception as exc:
        log_fail("获取页面状态", repr(exc))
        return PageState(
            url=getattr(page, "url", ""),
            title="",
            ready_state=None,
            is_closed=page.is_closed() if page else True,
        )


def get_session_state(
    session_id: Optional[str],
    *,
    valid: bool,
    created: bool = False,
    reused: bool = False,
) -> SessionState:
    """按当前会话管理器字段构建响应中的 SessionState。"""
    return SessionState(
        browser_session_id=session_id,
        valid=valid,
        created=created,
        reused=reused,
    )


def get_runtime_state(
    *,
    provider: str,
    engine: str,
    sandboxed: bool,
    mode: str,
) -> BrowserRuntimeState:
    """按当前浏览器运行形态构建响应中的 BrowserRuntimeState。"""
    return BrowserRuntimeState(
        provider=provider,
        engine=engine,
        sandboxed=sandboxed,
        mode=mode,
    )
