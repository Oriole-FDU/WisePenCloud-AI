import json
import uuid
import base64
from typing import Optional, Dict, Any, Tuple, List, Union
from pathlib import Path

from playwright.async_api import async_playwright, Page, Playwright, Browser

from chat.domain.interfaces.tool import BaseTool
from chat.application.web_fetch.content_cleaner import ContentCleaner
from common.logger import log_fail, log_ok


_SCROLL_STEP_PX = 100              # 每次滚动像素数
_FILL_FOCUS_WAIT_MS = 100           # fill 前等待元素获焦的毫秒数
_SCREENSHOT_JPEG_QUALITY = 40       # 截图 JPEG 压缩质量 (1-100)
_NAVIGATION_TIMEOUT_MS = 60000      # 页面导航超时（毫秒）
_SESSION_ID_LENGTH = 12             # 会话标识符截取长度
_LABEL_MAX_LENGTH = 80              # 快照中元素标签最大字符数
_FILL_LOG_TEXT_MAX_LENGTH = 40      # fill 操作日志中文本截断长度
_REDIRECT_INDICATORS = ("login.", "accounts.")  # 判定会话被重定向到登录页的关键词


class BrowseInteractTool(BaseTool):
    """
    浏览器交互工具

    - 基于 snapshot + ref 范式，每次调用执行一个 action
    - 使用本地 Playwright（Chromium）
    - 支持私有化模式：传入 user_data_dir 复用用户本地 Chrome 数据
    """

    def __init__(self, user_data_dir: Optional[Union[str, Path]] = None, timeout: int = 30):
        self._user_data_dir = str(user_data_dir) if user_data_dir else None  # 用户 Chrome 数据目录，用于私有化模式
        self._timeout = timeout
        self._local_session_id: Optional[str] = None
        self._local_playwright: Optional[Playwright] = None
        self._local_browser: Optional[Browser] = None
        self._local_page: Optional[Page] = None

        self._last_snapshot: Optional[str] = None
        self._last_screenshot: Optional[str] = None
        self._cleaner = ContentCleaner()

        self._action_handlers = {
            "navigate":     self._navigate,
            "go_back":      self._go_back,
            "go_forward":   self._go_forward,
            "snapshot":     self._snapshot_ref,
            "click_ref":    self._click_ref,
            "fill_ref":     self._fill_ref,
            "scroll":       self._scroll,
            "key":          self._key,
            "wait":         self._wait,
            "get_content":  self._get_content,
        }

    # -- Tool Interface --

    @property
    def name(self) -> str:
        return "browse_interact"

    @property
    def description(self) -> str:
        return (
            "Browser interaction tool based on snapshot+ref. "
            "Operates a local Chromium browser. "
            "Each call performs ONE action. "
            "Flow: 1) navigate to a page, 2) snapshot to get interactive elements "
            "with refs like [e1] textbox \"search\", 3) use click_ref or fill_ref "
            "with those refs to interact. "
            "Always pass browser_session_id from the previous response to keep the session alive."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "browser_session_id": {
                    "type": "string",
                    "description": (
                        "Pass the browser_session_id from the previous response "
                        "to keep the same session alive."
                    ),
                },
                "action": {
                    "type": "object",
                    "description": "A single action to perform. Use snapshot first to obtain refs, then interact one step at a time.",
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": [
                                "navigate", "go_back", "go_forward",
                                "snapshot", "click_ref", "fill_ref",
                                "scroll", "key", "wait", "screenshot",
                                "get_content"
                            ]
                        },
                        "url": {
                            "type": "string",
                            "description": "URL to navigate to (for navigate action)."
                        },
                        "ref": {
                            "type": "string",
                            "description": "Element ref from snapshot (e.g. 'e1'). Used by click_ref and fill_ref."
                        },
                        "text": {
                            "type": "string",
                            "description": "Text to fill (fill_ref) or key combination (key action)."
                        },
                        "scroll_direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"]
                        },
                        "scroll_amount": {
                            "type": "integer",
                            "description": "Number of scroll steps (100px each)."
                        },
                        "duration": {
                            "type": "number",
                            "description": "Duration in seconds (wait action)."
                        },
                    },
                    "required": ["type"]
                }
            },
            "required": []
        }

    # -- Execute --

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        browser_session_id: Optional[str] = kwargs.get("browser_session_id")
        action: Optional[Dict] = kwargs.get("action")

        if not browser_session_id and action and action.get("browser_session_id"):
            browser_session_id = action["browser_session_id"]

        try:
            page = await self._ensure_local_page(browser_session_id)
        except Exception as e:
            log_fail("浏览器交互", f"创建或恢复会话失败: {e}")
            return json.dumps({"success": False, "error": "Failed to create or restore browser session"})

        if not action:
            return json.dumps({"success": False, "error": "No action provided"})

        log_lines: List[str] = []

        act_type = action.get("type", "")
        if act_type == "navigate":
            url = action.get("url", "")
            if not url.startswith(("http://", "https://")):
                url = "https://" + url
            nav_result = await self._navigate_initial(page, url)
            if nav_result is not None:
                return nav_result
            log_lines.append(f"Navigated to {url}")
            return self._build_result(log_lines, url)

        actions_result = await self._execute_action(page, action, log_lines)
        if actions_result is not None:
            return actions_result

        return self._build_result(log_lines, None)

    async def _navigate_initial(self, page: Page, url: str) -> Optional[str]:
        try:
            await page.goto(url, wait_until="networkidle", timeout=_NAVIGATION_TIMEOUT_MS)
            return None
        except Exception as e:
            log_fail("浏览器交互", f"导航失败: {e}", url=url)
            return json.dumps({"success": False, "error": f"Navigation failed: {e}"})

    async def _execute_action(
        self, page: Page, action: Dict, log_lines: List[str]
    ) -> Optional[str]:
        try:
            action_log, snapshot_text, screenshot = await self._run_action(page, action)
            log_lines.append(action_log)
        except Exception as e:
            log_fail("浏览器交互", f"执行操作失败: {e}")
            if not await self._validate_page_alive():
                self._local_session_id = None
                self._local_page = None
            return json.dumps({"success": False, "error": f"Action execution failed: {e}"})

        self._last_snapshot = snapshot_text
        self._last_screenshot = screenshot
        return None

    def _build_result(self, log_lines: List[str], url: Optional[str]) -> str:
        result: Dict[str, Any] = {
            "success": True,
            "actions_log": log_lines,
            "browser_session_id": self._local_session_id,
        }
        if self._last_snapshot is not None:
            result["snapshot"] = self._last_snapshot
        if self._last_screenshot is not None:
            result["screenshot"] = self._last_screenshot
        log_ok("浏览器交互", url=url)
        return json.dumps(result, ensure_ascii=False)

    # -- Session Management --

    async def _ensure_local_page(self, browser_session_id: Optional[str]) -> Page:
        if await self._is_session_reusable(browser_session_id):
            return self._local_page

        await self._cleanup_local()

        self._local_playwright = await async_playwright().start()
        
        # 构建浏览器启动参数
        launch_options = {
            "headless": False,  # 用户必须能看到浏览器
            "channel": None,    # 使用系统默认 Chromium
            "args": ['--no-sandbox', '--disable-dev-shm-usage', '--disable-infobars'],
        }
        if self._user_data_dir:
            launch_options["args"].append(f'--user-data-dir={self._user_data_dir}')

        self._local_browser = await self._local_playwright.chromium.launch(**launch_options)
        self._local_page = await self._local_browser.new_page()
        self._local_session_id = uuid.uuid4().hex[:_SESSION_ID_LENGTH]
        return self._local_page

    async def _is_session_reusable(self, browser_session_id: Optional[str]) -> bool:
        if not (self._local_page and self._local_session_id):
            return False
        if self._local_session_id != browser_session_id:
            return False
        return await self._validate_page_alive()

    async def _validate_page_alive(self) -> bool:
        try:
            await self._local_page.evaluate("() => 1")
            current_url = self._local_page.url
            if any(indicator in current_url for indicator in _REDIRECT_INDICATORS):
                return False
            return True
        except Exception:
            return False

    async def _cleanup_local(self):
        cleanup_steps = [
            (self._local_page, "close", "page"),
            (self._local_browser, "close", "browser"),
            (self._local_playwright, "stop", "playwright"),
        ]
        for resource, method_name, label in cleanup_steps:
            if resource is None:
                continue
            try:
                await getattr(resource, method_name)()
            except Exception as e:
                log_fail("浏览器资源清理", f"关闭{label}失败: {e}")
        self._local_page = None
        self._local_browser = None
        self._local_playwright = None

    # -- Action Pipeline --

    async def _run_action(
        self, page: Page, action: Dict
    ) -> Tuple[str, Optional[str], Optional[str]]:
        act_type = action.get("type", "")
        snapshot_text: Optional[str] = None
        screenshot: Optional[str] = None

        if act_type == "screenshot":
            screenshot = await self._take_screenshot(page)
            return ("[screenshot]", None, screenshot)

        handler = self._action_handlers.get(act_type)
        if not handler:
            return (f"[skipped] unknown action: {act_type}", None, None)

        result = await handler(page, action)

        if isinstance(result, tuple):
            desc, snapshot_text = result
            return (desc, snapshot_text, screenshot)

        return (result, None, None)

    # -- Helpers --

    def _split_keys(self, key_combo: Optional[str]) -> List[str]:
        if not key_combo:
            return []
        return [part.strip() for part in key_combo.split("+") if part.strip()]

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
        upper_key = key.strip().upper()
        return synonyms.get(upper_key, key)

    def _normalize_keys(self, keys: List[str]) -> List[str]:
        return [self._normalize_key(k) for k in keys]

    async def _take_screenshot(self, page: Page) -> str:
        screenshot_bytes = await page.screenshot(
            type="jpeg",
            quality=_SCREENSHOT_JPEG_QUALITY,
            scale="css",
        )
        return base64.b64encode(screenshot_bytes).decode()

    # -- Snapshot + Ref Handlers --

    async def _snapshot_ref(self, page: Page, action: Dict) -> Tuple[str, str]:
        raw = await page.evaluate(_SNAPSHOT_JS)
        data = json.loads(raw)
        tree = data.get("tree", "(empty)")
        return ("Snapshot taken", tree)

    async def _click_ref(self, page: Page, action: Dict) -> str:
        ref = action.get("ref", "")
        if not ref:
            return "[click_ref] missing ref"
        el = await page.query_selector(f'[data-ref="{ref}"]')
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
        el = await page.query_selector(f'[data-ref="{ref}"]')
        if not el:
            return f"[fill_ref] ref '{ref}' not found"
        await el.scroll_into_view_if_needed()
        await el.click()
        await page.wait_for_timeout(_FILL_FOCUS_WAIT_MS)
        await el.fill(text)
        await page.keyboard.press("Escape")
        return f"Filled [{ref}]: '{text[:_FILL_LOG_TEXT_MAX_LENGTH]}'"

    # -- Navigation Handlers --

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

    # -- Scroll Handler --

    async def _scroll(self, page: Page, action: Dict) -> str:
        direction = action.get("scroll_direction", "down")
        amount = action.get("scroll_amount", 1)
        scroll_offsets = {
            "down": (0, _SCROLL_STEP_PX * amount),
            "up": (0, -_SCROLL_STEP_PX * amount),
            "right": (_SCROLL_STEP_PX * amount, 0),
            "left": (-_SCROLL_STEP_PX * amount, 0),
        }
        delta_x, delta_y = scroll_offsets[direction]
        await page.mouse.wheel(delta_x, delta_y)
        return f"Scrolled {direction} by {amount} step(s)"

    # -- Keyboard Handler --

    async def _key(self, page: Page, action: Dict) -> str:
        keys_str = action.get("text", "")
        keys = self._normalize_keys(self._split_keys(keys_str))
        if len(keys) == 1:
            await page.keyboard.press(keys[0])
            return f"Pressed key: {keys[0]}"
        for key in keys:
            await page.keyboard.down(key)
        for key in reversed(keys):
            await page.keyboard.up(key)
        return f"Pressed combo: {'+'.join(keys)}"

    # -- Utility Handler --

    async def _wait(self, page: Page, action: Dict) -> str:
        duration = action.get("duration", 1.0)
        await page.wait_for_timeout(duration * 1000)
        return f"Waited {duration}s"

    async def _get_content(self, page: Page, action: Dict) -> str:
        html = await page.content()
        cleaned = self._cleaner.clean(html)
        return cleaned if cleaned else "[get_content] No readable content found"


_SNAPSHOT_JS = """() => {
    const out = [];
    let idx = 0;
    const skip = new Set(['SCRIPT','STYLE','NOSCRIPT','SVG','HEAD','META','LINK',
                          'PATH','CIRCLE','RECT','POLYGON','USE','DEFS','G','BR','HR']);

    document.querySelectorAll('[data-ref]').forEach(el => el.removeAttribute('data-ref'));

    function isVisible(el) {
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 && rect.height === 0) return false;
        return true;
    }

    function isClickable(el) {
        if (el.disabled || el.getAttribute('aria-disabled') === 'true') return false;
        const style = window.getComputedStyle(el);
        if (style.pointerEvents === 'none') return false;
        const rect = el.getBoundingClientRect();
        if (rect.width === 0 || rect.height === 0) return false;
        const cx = rect.left + rect.width / 2;
        const cy = rect.top + rect.height / 2;
        const topEl = document.elementFromPoint(cx, cy);
        if (topEl && topEl !== el && !el.contains(topEl) && !topEl.contains(el)) return false;
        return true;
    }

    function getRole(el) {
        const r = el.getAttribute('role') || '';
        const t = el.tagName.toLowerCase();
        const type = (el.getAttribute('type') || '').toLowerCase();
        if (r) return r;
        if (t === 'input') {
            if (type === 'hidden') return '';
            return type === 'submit' || type === 'button' ? 'button'
                 : type === 'checkbox' ? 'checkbox'
                 : type === 'radio' ? 'radio' : 'textbox';
        }
        if (t === 'textarea') return 'textbox';
        if (t === 'select') return 'combobox';
        if (t === 'button') return 'button';
        if (t === 'a' && el.href) return 'link';
        if (t === 'img') return 'img';
        if (t === 'video') return 'video';
        return '';
    }

    function getLabel(el) {
        const aria = el.getAttribute('aria-label')
                  || el.getAttribute('placeholder')
                  || el.getAttribute('title')
                  || el.getAttribute('name') || '';
        if (aria) return aria.trim().slice(0, """ + str(_LABEL_MAX_LENGTH) + """);
        const lab = el.getAttribute('aria-labelledby');
        if (lab) {
            const lbl = document.getElementById(lab);
            if (lbl) {
                const t = (lbl.innerText || lbl.textContent || '').trim().slice(0, """ + str(_LABEL_MAX_LENGTH) + """);
                if (t) return t;
            }
        }
        if (el.id) {
            const labelEl = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (labelEl) {
                const t = (labelEl.innerText || labelEl.textContent || '').trim().slice(0, """ + str(_LABEL_MAX_LENGTH) + """);
                if (t) return t;
            }
        }
        const selfText = (el.innerText || el.textContent || '').trim().slice(0, """ + str(_LABEL_MAX_LENGTH) + """);
        return selfText;
    }

    function walk(root, depth) {
        const children = root.shadowRoot ? root.shadowRoot.children : root.children;
        if (!children) return;
        for (const el of children) {
            if (!el || !el.tagName || skip.has(el.tagName)) continue;
            const tag = el.tagName.toLowerCase();
            if (tag === 'input' && (el.getAttribute('type') || '').toLowerCase() === 'hidden') continue;
            const role = getRole(el);
            if (!role) { walk(el, depth + 1); continue; }
            if (!isVisible(el)) continue;
            if (tag === 'iframe') {
                const id = 'e' + (++idx);
                out.push('  '.repeat(depth) + '[' + id + '] iframe (cross-origin)');
                continue;
            }
            const id = 'e' + (++idx);
            el.setAttribute('data-ref', id);
            const label = getLabel(el);
            let line = '  '.repeat(depth) + '[' + id + '] ' + role;
            if (label) line += ' "' + label + '"';
            if (!isClickable(el)) line += ' [disabled]';
            out.push(line);
            walk(el, depth + 1);
        }
    }

    walk(document.body, 0);
    return JSON.stringify({ tree: out.join('\\n') });
}"""
