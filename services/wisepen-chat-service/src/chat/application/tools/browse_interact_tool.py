import os
import json
import uuid
import base64
from typing import Optional, Dict, Any, Tuple, List, Literal

from steel import AsyncSteel
from playwright.async_api import async_playwright, Page

from chat.domain.interfaces.tool import BaseTool
from chat.core.config.app_settings import settings
from common.logger import log_fail, log_ok

_SNAPSHOT_JS = """() => {
    const out = [];
    const refs = [];
    let idx = 0;
    const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','IFRAME','SVG','HEAD','META','LINK','PATH','CIRCLE','RECT','POLYGON','USE','DEFS','G','BR','HR']);
    function getRole(el) {
        const r = el.getAttribute('role') || '';
        const t = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (r) return r;
        if (t === 'input') return type === 'submit' || type === 'button' ? 'button' : type === 'checkbox' ? 'checkbox' : type === 'radio' ? 'radio' : 'textbox';
        if (t === 'textarea') return 'textbox';
        if (t === 'select') return 'combobox';
        if (t === 'button') return 'button';
        if (t === 'a' && el.href) return 'link';
        if (t === 'img') return 'img';
        if (t === 'video') return 'video';
        return t;
    }
    function getLabel(el) {
        const aria = el.getAttribute('aria-label') || el.getAttribute('placeholder') || el.getAttribute('title') || el.getAttribute('name') || '';
        if (aria) return aria.trim();
        const lab = el.getAttribute('aria-labelledby');
        if (lab) {
            const lbl = document.getElementById(lab);
            if (lbl) return (lbl.innerText || lbl.textContent || '').trim().slice(0, 60);
        }
        const t = (el.innerText || el.textContent || '').trim().slice(0, 60);
        return t;
    }
    function walk(el, depth) {
        if (!el || !el.tagName || skip.has(el.tagName)) return;
        const tag = el.tagName.toLowerCase();
        const role = getRole(el);
        const isInteractive = new Set(['input','textarea','select','button','a','video','details','summary']).has(tag) || el.hasAttribute('onclick') || el.getAttribute('contenteditable') === 'true' || (el.tabIndex >= 0 && tag !== 'body');
        if (isInteractive) {
            const id = 'e' + (++idx);
            el.setAttribute('data-ref', id);
            refs.push({ref: id, s: '#' + CSS.escape('data-ref-' + id)});
            const label = getLabel(el);
            let line = `  ${'  '.repeat(depth)}[${id}] ${role}`;
            if (label) line += ` "${label}"`;
            out.push(line);
        }
        for (const child of el.children) {
            walk(child, depth + 1);
        }
    }
    walk(document.body, 0);
    return JSON.stringify({tree: out.join('\\n'), refs: JSON.stringify(refs)});
}"""


class BrowseInteractTool(BaseTool):
    """agent-browser 思路的 Playwright 原生实现: snapshot + ref 替代截图 + 坐标

    执行链路:
        Steel 云浏览器 → 本地 Playwright (Edge/Chrome)

    操作模式（按 Token 效率排序）:
        1) snapshot → 获取页面可交互元素树（纯文本 ~200 token）
        2) click_ref / fill_ref → 基于 ref 精确操作
        3) 坐标模式 → 兜底（left_click, type, scroll 等）
    """

    def __init__(self, timeout: int = 30):
        self._steel = AsyncSteel(
            base_url=settings.STEEL_BASE_URL,
            timeout=timeout,
        )

        # 持久化本地会话
        self._local_sid: Optional[str] = None
        self._local_playwright = None
        self._local_browser = None
        self._local_page: Optional[Page] = None

        self._action_handlers = {
            "mouse_move":          self._mouse_move,
            "left_click":          self._left_click,
            "right_click":         self._right_click,
            "middle_click":        self._middle_click,
            "double_click":        self._double_click,
            "triple_click":        self._triple_click,
            "left_mouse_down":     self._left_mouse_down,
            "left_mouse_up":       self._left_mouse_up,
            "left_click_drag":     self._left_click_drag,
            "scroll":              self._scroll,
            "key":                 self._key,
            "hold_key":            self._hold_key,
            "type":                self._type_text,
            "wait":                self._wait,
            "navigate":            self._navigate,
            "go_back":             self._go_back,
            "go_forward":          self._go_forward,
            "find_and_click":      self._find_and_click,
            "snapshot":            self._snapshot_ref,
            "click_ref":           self._click_ref,
            "fill_ref":            self._fill_ref,
        }

        self._execution_chain: List[Tuple] = [
            (self._execute_steel, "Steel"),
            (self._execute_local, "LocalPlaywright"),
        ]

    @property
    def name(self) -> str:
        return "browse_interact"

    @property
    def description(self) -> str:
        return (
            "Interact with a web page using snapshot+ref (preferred, token-efficient) or coordinate actions. "
            "Flow: 1) navigate to page, 2) snapshot to see interactive elements with refs like [e1] textbox \"search\", "
            "3) click_ref or fill_ref by ref, 4) optionally screenshot to confirm. "
            "Rules: one action per call; pass browser_session_id from previous response."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "browser_session_id": {
                    "type": "string",
                    "description": (
                        "CRITICAL: Pass the `browser_session_id` from the PREVIOUS response to keep the same browser session alive. "
                        "If you omit this, a NEW empty browser will be created. "
                        "Always include this field from the second call onward."
                    ),
                },
                "url": {
                    "type": "string",
                    "description": "The URL to open when creating a new session (first call only)."
                },
                "actions": {
                    "type": "array",
                    "description": "Actions to perform. For multi-step operations, use snapshot to get refs, then click_ref/fill_ref with those refs.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "type": {
                                "type": "string",
                                "enum": [
                                    "snapshot",
                                    "click_ref",
                                    "fill_ref",
                                    "mouse_move",
                                    "left_click",
                                    "right_click",
                                    "middle_click",
                                    "double_click",
                                    "triple_click",
                                    "left_mouse_down",
                                    "left_mouse_up",
                                    "left_click_drag",
                                    "scroll",
                                    "key",
                                    "hold_key",
                                    "type",
                                    "wait",
                                    "screenshot",
                                    "navigate",
                                    "go_back",
                                    "go_forward",
                                    "find_and_click",
                                ]
                            },
                            "ref": {
                                "type": "string",
                                "description": "Element ref from snapshot (e.g. 'e1'). Used by click_ref and fill_ref.",
                            },
                            "text": {
                                "type": "string",
                                "description": "Text to type (type action) or fill (fill_ref action), or key combo (key action).",
                            },
                            "selector": {
                                "type": "string",
                                "description": "CSS selector for find_and_click.",
                            },
                            "coordinate": {
                                "type": "array",
                                "items": {"type": "integer"},
                                "description": "[x, y] pixel coordinates for mouse and scroll actions.",
                            },
                            "scroll_direction": {
                                "type": "string",
                                "enum": ["up", "down", "left", "right"],
                            },
                            "scroll_amount": {"type": "integer", "description": "Scroll steps (100px each)."},
                            "duration": {"type": "number", "description": "Duration in seconds (wait, hold_key)."},
                            "key": {
                                "type": "string",
                                "description": "Modifier key(s) to hold, e.g. 'Shift' or 'Control+Alt'.",
                            },
                            "screenshot": {
                                "type": "boolean",
                                "default": False,
                                "description": "Set true to take a screenshot after this action. Use sparingly.",
                            },
                        },
                        "required": ["type"],
                    },
                },
            },
            "required": [],
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        # session_id 从系统注入的 context 读取
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        browser_session_id: Optional[str] = kwargs.get("browser_session_id")
        url: Optional[str] = kwargs.get("url")
        actions: List = kwargs.get("actions", [])

        for executor, executor_name in self._execution_chain:
            try:
                result = await executor(browser_session_id, url, actions)
            except Exception as e:
                log_fail("浏览器交互", e, url=url, executor=executor_name)
                continue

            if result is None:
                log_fail("浏览器交互", "执行结果为空", url=url, executor=executor_name)
                continue

            log_ok("浏览器交互", url=url, executor=executor_name)
            return result

        log_fail("浏览器交互", "所有执行器均失败", url=url)
        return json.dumps({"success": False, "error": "All browser executors exhausted"})


    # ═══════════════════════════════════════════════════════════════════════════
    # Executors
    # ═══════════════════════════════════════════════════════════════════════════

    # 本地方案
    async def _execute_local(self, browser_session_id: Optional[str], url: Optional[str], actions: List) -> Optional[str]:
        log: List[str] = []
        page = await self._ensure_local_page(browser_session_id, log)
        sid = self._local_sid

        if url:
            await page.goto(url, wait_until="networkidle", timeout=60000)
            log.append(f"Navigated to {url}")

        try:
            actions_log, snapshot_text, last_screenshot = await self._run_actions(page, actions)
            log.extend(actions_log)
        except Exception:
            self._local_sid = None
            self._local_page = None
            return None

        return self._build_result(sid, log, snapshot_text, last_screenshot)

    # Steel 方案
    async def _execute_steel(self, browser_session_id: Optional[str], url: Optional[str], actions: List) -> Optional[str]:
        if browser_session_id:
            try:
                await self._steel.sessions.retrieve(browser_session_id)
            except Exception:
                new_session = await self._steel.sessions.create()
                browser_session_id = new_session.id
        else:
            new_session = await self._steel.sessions.create()
            browser_session_id = new_session.id

        session = await self._steel.sessions.retrieve(browser_session_id)
        websocket_url = session.websocket_url
        log: List[str] = []

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(websocket_url)
            page = browser.contexts[0].pages[0] if browser.contexts else await browser.new_page()

            if url:
                await page.goto(url, wait_until="networkidle")
                log.append(f"Navigated to {url}")

            try:
                actions_log, snapshot_text, last_screenshot = await self._run_actions(page, actions)
                log.extend(actions_log)
            except Exception:
                return None

        return self._build_result(browser_session_id, log, snapshot_text, last_screenshot)


    def _build_result(self, session_id: str, actions_log: List[str], snapshot_text: Optional[str] = None, screenshot: Optional[str] = None) -> str:
        result: Dict[str, Any] = {"success": True, "browser_session_id": session_id, "actions_log": actions_log}
        if snapshot_text is not None:
            result["snapshot"] = snapshot_text
        if screenshot:
            result["screenshot"] = screenshot
        return json.dumps(result, ensure_ascii=False)


    async def _run_actions(self, page: Page, actions: List) -> Tuple[List[str], Optional[str], Optional[str]]:
        actions_log: List[str] = []
        snapshot_text: Optional[str] = None
        last_screenshot: Optional[str] = None

        for action in actions:
            act_type = action["type"]
            if act_type == "screenshot":
                last_screenshot = await self._take_screenshot(page)
                actions_log.append("[screenshot]")
                continue
            handler = self._action_handlers.get(act_type)
            if not handler:
                actions_log.append(f"[skipped] unknown action: {act_type}")
                continue
            result = await handler(page, action)
            if isinstance(result, tuple):
                desc, snapshot_text = result
                actions_log.append(desc)
            else:
                actions_log.append(result)
            if action.get("screenshot"):
                last_screenshot = await self._take_screenshot(page)

        return actions_log, snapshot_text, last_screenshot


    # ═══════════════════════════════════════════════════════════════════════════
    # Local Session Helpers
    # ═══════════════════════════════════════════════════════════════════════════

    async def _ensure_local_page(self, browser_session_id: Optional[str], actions_log: List[str]) -> Page:
        if self._local_page and self._local_sid and self._local_sid == browser_session_id:
            try:
                await self._local_page.evaluate("() => 1")
                return self._local_page
            except Exception:
                actions_log.append("[session dead, recreating browser]")

        # 关闭旧的实例
        if self._local_browser:
            try:
                await self._local_browser.close()
            except Exception:
                pass
        if self._local_playwright:
            try:
                await self._local_playwright.stop()
            except Exception:
                pass

        self._local_playwright = await async_playwright().start()
        self._local_browser = await self._launch_browser()
        self._local_page = await self._local_browser.new_page()
        self._local_sid = uuid.uuid4().hex[:12]
        return self._local_page

    async def _launch_browser(self):
        for channel in ("chrome", "msedge", None):
            try:
                return await self._local_playwright.chromium.launch(channel=channel, headless=False)
            except Exception:
                continue
        return await self._local_playwright.chromium.launch(headless=False)


    # ═══════════════════════════════════════════════════════════════════════════
    # Helpers
    # ═══════════════════════════════════════════════════════════════════════════
    def _center(self) -> Tuple[int, int]:
        return (900, 540)

    def _get_coordinate(self, action: Dict) -> Tuple[int, int]:
        coord = action.get("coordinate")
        if coord and isinstance(coord, (list, tuple)) and len(coord) == 2:
            return int(coord[0]), int(coord[1])
        return self._center()

    def _split_keys(self, k: Optional[str]) -> List[str]:
        if not k:
            return []
        return [s.strip() for s in k.split("+") if s.strip()]

    def _normalize_key(self, key: str) -> str:
        synonyms = {
            "ENTER": "Enter", "RETURN": "Enter",
            "ESC": "Escape", "ESCAPE": "Escape",
            "TAB": "Tab", "BACKSPACE": "Backspace",
            "DELETE": "Delete", "DEL": "Delete",
            "SPACE": "Space", "CTRL": "Control",
            "CONTROL": "Control", "ALT": "Alt",
            "SHIFT": "Shift", "META": "Meta",
            "SUPER": "Meta", "CMD": "Meta",
            "COMMAND": "Meta", "UP": "ArrowUp",
            "DOWN": "ArrowDown", "LEFT": "ArrowLeft",
            "RIGHT": "ArrowRight", "HOME": "Home",
            "END": "End", "PAGEUP": "PageUp",
            "PAGEDOWN": "PageDown", "INSERT": "Insert",
            "ARROWUP": "ArrowUp", "ARROWDOWN": "ArrowDown",
            "ARROWLEFT": "ArrowLeft", "ARROWRIGHT": "ArrowRight",
        }
        k = key.strip().upper()
        return synonyms.get(k, key)

    def _normalize_keys(self, keys: List[str]) -> List[str]:
        return [self._normalize_key(k) for k in keys]

    async def _take_screenshot(self, page: Page) -> str:
        screenshot_bytes = await page.screenshot(type="jpeg", quality=40, scale="css")
        return base64.b64encode(screenshot_bytes).decode()


    # ═══════════════════════════════════════════════════════════════════════════
    # Snapshot + Ref Handlers
    # ═══════════════════════════════════════════════════════════════════════════
    async def _snapshot_ref(self, page: Page, action: Dict) -> Tuple[str, str]:
        raw = await page.evaluate(_SNAPSHOT_JS)
        data = json.loads(raw)
        tree = data.get("tree", "(empty)")
        return ("Snapshot taken", tree)

    async def _click_ref(self, page: Page, action: Dict) -> str:
        ref = action.get("ref", "")
        if not ref:
            return "[click_ref] missing ref"
        el = await page.query_selector(f"[data-ref=\"{ref}\"]")
        if not el:
            return f"[click_ref] ref '{ref}' not found"
        await el.scroll_into_view_if_needed()
        box = await el.bounding_box()
        if box:
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            await page.mouse.click(x, y)
            return f"Clicked [{ref}] at ({int(x)}, {int(y)})"
        await el.click()
        return f"Clicked [{ref}]"

    async def _fill_ref(self, page: Page, action: Dict) -> str:
        ref = action.get("ref", "")
        text = action.get("text", "")
        if not ref:
            return "[fill_ref] missing ref"
        el = await page.query_selector(f"[data-ref=\"{ref}\"]")
        if not el:
            return f"[fill_ref] ref '{ref}' not found"
        await el.scroll_into_view_if_needed()
        await el.click()
        await el.fill("")
        await el.type(text)
        return f"Filled [{ref}]: '{text[:40]}'"


    # ═══════════════════════════════════════════════════════════════════════════
    # Advanced Click Handlers
    # ═══════════════════════════════════════════════════════════════════════════
    async def _find_and_click(self, page: Page, action: Dict) -> str:
        selector = action.get("selector", "")
        if not selector:
            return "[find_and_click] missing selector"
        el = await page.query_selector(selector)
        if not el:
            return f"[find_and_click] no element found for: {selector}"
        await el.scroll_into_view_if_needed()
        box = await el.bounding_box()
        if box:
            x = box["x"] + box["width"] / 2
            y = box["y"] + box["height"] / 2
            await page.mouse.click(x, y)
            return f"Clicked '{selector}' at ({int(x)}, {int(y)})"
        await el.click()
        return f"Clicked '{selector}' (via element.click)"


    # ═══════════════════════════════════════════════════════════════════════════
    # Mouse Handlers
    # ═══════════════════════════════════════════════════════════════════════════
    async def _mouse_move(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.move(x, y)
        return f"Mouse moved to ({x}, {y})"

    async def _left_click(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.click(x, y, button="left")
        return f"Left clicked at ({x}, {y})"

    async def _right_click(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.click(x, y, button="right")
        return f"Right clicked at ({x}, {y})"

    async def _middle_click(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.click(x, y, button="middle")
        return f"Middle clicked at ({x}, {y})"

    async def _double_click(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.dblclick(x, y)
        return f"Double clicked at ({x}, {y})"

    async def _triple_click(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        for _ in range(3):
            await page.mouse.click(x, y)
        return f"Triple clicked at ({x}, {y})"

    async def _left_mouse_down(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.move(x, y)
        await page.mouse.down(button="left")
        return f"Mouse down at ({x}, {y})"

    async def _left_mouse_up(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        await page.mouse.move(x, y)
        await page.mouse.up(button="left")
        return f"Mouse up at ({x}, {y})"

    async def _left_click_drag(self, page: Page, action: Dict) -> str:
        start_x, start_y = self._center()
        end_x, end_y = self._get_coordinate(action)
        await page.mouse.move(start_x, start_y)
        await page.mouse.down()
        await page.mouse.move(end_x, end_y, steps=10)
        await page.mouse.up()
        return f"Dragged from ({start_x}, {start_y}) to ({end_x}, {end_y})"

    async def _scroll(self, page: Page, action: Dict) -> str:
        x, y = self._get_coordinate(action)
        direction = action.get("scroll_direction", "down")
        amount = action.get("scroll_amount", 1)
        step = 100
        dx, dy = {
            "down": (0, step * amount),
            "up": (0, -step * amount),
            "right": (step * amount, 0),
            "left": (-step * amount, 0),
        }[direction]
        await page.mouse.wheel(dx, dy)
        return f"Scrolled {direction} by {amount} step(s)"


    # ═══════════════════════════════════════════════════════════════════════════
    # Keyboard Handlers
    # ═══════════════════════════════════════════════════════════════════════════
    async def _key(self, page: Page, action: Dict) -> str:
        keys_str = action.get("text", "")
        keys = self._normalize_keys(self._split_keys(keys_str))
        if len(keys) == 1:
            await page.keyboard.press(keys[0])
            return f"Pressed key: {keys[0]}"
        else:
            for key in keys:
                await page.keyboard.down(key)
            for key in reversed(keys):
                await page.keyboard.up(key)
            return f"Pressed combo: {'+'.join(keys)}"

    async def _hold_key(self, page: Page, action: Dict) -> str:
        keys_str = action.get("text", "")
        keys = self._normalize_keys(self._split_keys(keys_str))
        duration = action.get("duration", 1.0)
        for key in keys:
            await page.keyboard.down(key)
        await page.wait_for_timeout(duration * 1000)
        for key in reversed(keys):
            await page.keyboard.up(key)
        return f"Held {'+'.join(keys)} for {duration}s"

    async def _type_text(self, page: Page, action: Dict) -> str:
        text = action.get("text", "")
        if text.startswith("$"):
            text = os.getenv(text[1:], text)
        await page.keyboard.type(text)
        preview = text if len(text) <= 40 else text[:40] + "..."
        return f"Typed: '{preview}'"


    # ═══════════════════════════════════════════════════════════════════════════
    # Navigation and Utility Handlers
    # ═══════════════════════════════════════════════════════════════════════════
    async def _wait(self, page: Page, action: Dict) -> str:
        duration = action.get("duration", 1.0)
        await page.wait_for_timeout(duration * 1000)
        return f"Waited {duration}s"

    async def _navigate(self, page: Page, action: Dict) -> str:
        url = action.get("url") or action.get("text", "")
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        await page.goto(url, wait_until="networkidle")
        title = await page.title()
        return f"Navigated to: {title} ({url})"

    async def _go_back(self, page: Page, action: Dict) -> str:
        await page.go_back()
        return "Went back"

    async def _go_forward(self, page: Page, action: Dict) -> str:
        await page.go_forward()
        return "Went forward"
