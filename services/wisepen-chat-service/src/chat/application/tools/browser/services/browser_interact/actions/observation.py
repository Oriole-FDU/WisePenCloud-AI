import base64
from typing import Dict, Optional

from chat.application.tools.browser.services.browser_interact.enums import (
    ActionStatus,
    BrowserActionType,
    ContentTrustLevel,
    DiagnosticCode,
    SnapshotMode,
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
)
from chat.application.tools.browser.services.browser_interact.runtime.action_runtime import (
    action_error_response,
    action_success_response,
    get_existing_page_or_error,
)
from chat.application.tools.browser.services.browser_interact.runtime.content import BrowserContentExtractor
from chat.application.tools.browser.services.browser_interact.runtime.intervention import (
    UserInterventionDetector,
)
from chat.application.tools.browser.services.browser_interact.runtime.session import BrowserSessionManager
from chat.application.tools.browser.services.browser_interact.snapshot.manager import SnapshotManager
from common.logger import log_fail

_SCREENSHOT_JPEG_QUALITY = 40
_REF_OVERLAY_STYLE_ID = "wisepen-ref-overlay-style"
_REF_OVERLAY_ATTR = "data-wisepen-ref-overlay"


async def _install_ref_overlay(page, refs: Dict[str, Dict]) -> int:
    """基于最新 snapshot bounds 在页面上临时绘制 ref 标注。"""
    overlay_refs = []
    for ref, metadata in refs.items():
        bounds = metadata.get("bounds")
        if not isinstance(bounds, dict) or not metadata.get("in_viewport"):
            continue
        overlay_refs.append(
            {
                "ref": ref,
                "x": bounds.get("x", 0),
                "y": bounds.get("y", 0),
                "width": bounds.get("width", 0),
                "height": bounds.get("height", 0),
            }
        )

    await page.evaluate(
        """({ refs, styleId, overlayAttr }) => {
            document.querySelectorAll(`[${overlayAttr}]`).forEach(el => el.remove());
            const oldStyle = document.getElementById(styleId);
            if (oldStyle) oldStyle.remove();

            const style = document.createElement('style');
            style.id = styleId;
            style.textContent = `
              [${overlayAttr}] {
                position: fixed;
                z-index: 2147483647;
                pointer-events: none;
                box-sizing: border-box;
                border: 2px solid #ff4d00;
                background: rgba(255, 77, 0, 0.08);
                color: #111;
                font: 12px/1.2 Arial, sans-serif;
              }
              [${overlayAttr}] > span {
                position: absolute;
                left: -2px;
                top: -18px;
                padding: 2px 4px;
                background: #ff4d00;
                color: white;
                border-radius: 3px;
              }
            `;
            document.documentElement.appendChild(style);

            for (const item of refs) {
                if (!item.width || !item.height) continue;
                const box = document.createElement('div');
                box.setAttribute(overlayAttr, 'true');
                box.style.left = `${item.x}px`;
                box.style.top = `${item.y}px`;
                box.style.width = `${item.width}px`;
                box.style.height = `${item.height}px`;
                const label = document.createElement('span');
                label.textContent = item.ref;
                box.appendChild(label);
                document.documentElement.appendChild(box);
            }
        }""",
        {
            "refs": overlay_refs,
            "styleId": _REF_OVERLAY_STYLE_ID,
            "overlayAttr": _REF_OVERLAY_ATTR,
        },
    )
    return len(overlay_refs)


async def _remove_ref_overlay(page) -> None:
    """移除 screenshot 前安装的临时 ref 标注。"""
    try:
        await page.evaluate(
            """({ styleId, overlayAttr }) => {
                document.querySelectorAll(`[${overlayAttr}]`).forEach(el => el.remove());
                const style = document.getElementById(styleId);
                if (style) style.remove();
            }""",
            {"styleId": _REF_OVERLAY_STYLE_ID, "overlayAttr": _REF_OVERLAY_ATTR},
        )
    except Exception:
        pass


async def handle_snapshot(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """采集当前页面的 accessibility snapshot 并生成可交互 refs。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    try:
        mode = action.get("mode")
        goal = action.get("goal")
        scope_ref = action.get("ref")

        if mode is None:
            mode = SnapshotMode.FOCUSED.value if goal else SnapshotMode.FULL.value

        if mode not in (SnapshotMode.FULL.value, SnapshotMode.FOCUSED.value):
            return await action_error_response(
                session_manager,
                page,
                error=make_schema_error("snapshot mode must be 'full' or 'focused'."),
            )

        limit = action.get("limit")
        snapshot_payload = await snapshot_manager.take(
            page,
            mode=mode,
            goal=goal,
            limit=limit,
            scope_ref=scope_ref,
        )
        dom_version = await session_manager.current_dom_version()
    except Exception as error:
        log_fail("浏览器快照", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.SNAPSHOT.value,
                message="Snapshot failed.",
                diagnostic_code=DiagnosticCode.SNAPSHOT_FAILED.value,
            ),
        )

    page_state = await get_page_state(page)
    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.SNAPSHOT.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "snapshot_id": snapshot_payload.snapshot_id,
                "mode": snapshot_payload.mode,
                "goal": snapshot_payload.goal,
                "scope_ref": snapshot_payload.scope_ref,
                "dom_version": dom_version,
                "content_trust": ContentTrustLevel.UNTRUSTED_PAGE_CONTENT.value,
            },
        ),
        snapshot=snapshot_payload,
    )


async def handle_screenshot(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """截取当前视口并返回 JPEG base64。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    annotate_refs = bool(action.get("annotate_refs", False))
    overlay_count = 0

    try:
        if annotate_refs:
            refs = snapshot_manager.current_refs_metadata()
            if not refs:
                return await action_error_response(
                    session_manager,
                    page,
                    error=make_schema_error(
                        "screenshot annotate_refs requires a current snapshot with refs."
                    ),
                )
            overlay_count = await _install_ref_overlay(page, refs)

        screenshot_bytes = await page.screenshot(
            type="jpeg",
            quality=_SCREENSHOT_JPEG_QUALITY,
            scale="css",
            full_page=False,
        )
        screenshot_b64 = base64.b64encode(screenshot_bytes).decode()
    except Exception as error:
        log_fail("浏览器截图", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.SCREENSHOT.value,
                message="Screenshot failed.",
                diagnostic_code=DiagnosticCode.SCREENSHOT_FAILED.value,
            ),
        )
    finally:
        if annotate_refs:
            await _remove_ref_overlay(page)

    page_state = await get_page_state(page)
    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.SCREENSHOT.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "annotate_refs": annotate_refs,
                "overlay_ref_count": overlay_count,
            },
        ),
        screenshot=screenshot_b64,
    )


async def handle_get_content(
    session_manager: BrowserSessionManager,
    snapshot_manager: SnapshotManager,
    intervention: UserInterventionDetector,
    content_extractor: BrowserContentExtractor,
    browser_session_id: Optional[str],
    action: Dict,
) -> str:
    """抽取当前页面正文，优先渲染文本，必要时回退 HTML 到 Markdown。"""
    page, session_error_response = await get_existing_page_or_error(
        session_manager,
        browser_session_id,
    )
    if session_error_response:
        return session_error_response

    try:
        content = await content_extractor.extract(page)
    except Exception as error:
        log_fail("页面内容提取", str(error))
        return await action_error_response(
            session_manager,
            page,
            error=make_action_failed_error(
                action_type=BrowserActionType.GET_CONTENT.value,
                message="Get content failed.",
                diagnostic_code=DiagnosticCode.GET_CONTENT_FAILED.value,
            ),
        )

    page_state = await get_page_state(page)

    return action_success_response(
        session_manager,
        page_state=page_state,
        reused=True,
        action_result=ActionResult(
            type=BrowserActionType.GET_CONTENT.value,
            status=ActionStatus.COMPLETED.value,
            detail={
                "content_trust": ContentTrustLevel.UNTRUSTED_PAGE_CONTENT.value,
                "content": content,
                "content_length": len(content),
            },
        ),
    )
