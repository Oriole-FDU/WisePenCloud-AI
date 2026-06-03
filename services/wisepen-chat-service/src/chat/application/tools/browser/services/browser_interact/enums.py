from enum import StrEnum


class BrowserActionType(StrEnum):
    NAVIGATE = "navigate"
    GO_BACK = "go_back"
    GO_FORWARD = "go_forward"
    NEW_TAB = "new_tab"
    LIST_TABS = "list_tabs"
    SWITCH_TAB = "switch_tab"
    CLOSE_TAB = "close_tab"
    SNAPSHOT = "snapshot"
    CLICK_REF = "click_ref"
    FILL_REF = "fill_ref"
    SELECT_REF = "select_ref"
    CHECK_REF = "check_ref"
    SCROLL = "scroll"
    KEY = "key"
    WAIT = "wait"
    WAIT_FOR_REF = "wait_for_ref"
    WAIT_FOR_TEXT = "wait_for_text"
    SCREENSHOT = "screenshot"
    GET_CONTENT = "get_content"
    STATUS = "status"
    CLEAR_BROWSER_EVENTS = "clear_browser_events"


class ErrorCategory(StrEnum):
    INVALID_REQUEST = "invalid_request"
    SESSION = "session"
    SNAPSHOT = "snapshot"
    ACTION = "action"
    USER_INTERVENTION = "user_intervention"
    BROWSER_UNAVAILABLE = "browser_unavailable"
    INTERNAL = "internal"


class ToolErrorCode(StrEnum):
    NO_ACTION = "NO_ACTION"
    INVALID_ACTION_SCHEMA = "INVALID_ACTION_SCHEMA"
    UNKNOWN_ACTION = "UNKNOWN_ACTION"

    SESSION_REQUIRED = "SESSION_REQUIRED"
    SESSION_NOT_FOUND = "SESSION_NOT_FOUND"
    SESSION_MISMATCH = "SESSION_MISMATCH"
    SESSION_EXPIRED = "SESSION_EXPIRED"

    SNAPSHOT_REQUIRED = "SNAPSHOT_REQUIRED"
    STALE_REF = "STALE_REF"
    REF_NOT_FOUND = "REF_NOT_FOUND"

    ACTION_FAILED = "ACTION_FAILED"
    USER_INTERVENTION_REQUIRED = "USER_INTERVENTION_REQUIRED"
    BROWSER_UNAVAILABLE = "BROWSER_UNAVAILABLE"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class DiagnosticCode(StrEnum):
    """内部诊断码，进入响应时保持字符串值稳定。"""

    BROWSER_SESSION_ERROR = "BROWSER_SESSION_ERROR"
    NO_DISPLAY_SERVER = "NO_DISPLAY_SERVER"
    BROWSER_CRASHED_ON_START = "BROWSER_CRASHED_ON_START"
    LAUNCH_TIMEOUT = "LAUNCH_TIMEOUT"
    BROWSER_LAUNCH_FAILED = "BROWSER_LAUNCH_FAILED"
    UNEXPECTED_SESSION_ERROR = "UNEXPECTED_SESSION_ERROR"
    UNHANDLED_CONTROLLER_ERROR = "UNHANDLED_CONTROLLER_ERROR"

    SNAPSHOT_FAILED = "SNAPSHOT_FAILED"
    SCREENSHOT_FAILED = "SCREENSHOT_FAILED"
    GET_CONTENT_FAILED = "GET_CONTENT_FAILED"
    NAVIGATION_TIMEOUT = "NAVIGATION_TIMEOUT"
    NAVIGATION_FAILED = "NAVIGATION_FAILED"
    TAB_OPERATION_FAILED = "TAB_OPERATION_FAILED"
    SCROLL_FAILED = "SCROLL_FAILED"
    KEY_FAILED = "KEY_FAILED"
    WAIT_FAILED = "WAIT_FAILED"
    WAIT_CONDITION_TIMEOUT = "WAIT_CONDITION_TIMEOUT"
    CLICK_TARGET_DETACHED = "CLICK_TARGET_DETACHED"
    CLICK_FAILED = "CLICK_FAILED"
    REF_NOT_FILLABLE = "REF_NOT_FILLABLE"
    FILL_FAILED = "FILL_FAILED"
    REF_NOT_SELECTABLE = "REF_NOT_SELECTABLE"
    SELECT_FAILED = "SELECT_FAILED"
    REF_NOT_CHECKABLE = "REF_NOT_CHECKABLE"
    CHECK_FAILED = "CHECK_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class RecoveryHintType(StrEnum):
    """错误响应中用于指导调用方恢复流程的动作类型。"""

    FIX_REQUEST = "fix_request"
    REFRESH_STATUS = "refresh_status"
    RESTART_SESSION = "restart_session"
    START_SESSION = "start_session"
    REFRESH_SNAPSHOT = "refresh_snapshot"
    INSPECT_PAGE_STATE = "inspect_page_state"
    WAIT_FOR_USER = "wait_for_user"
    RESTART_BROWSER_RUNTIME = "restart_browser_runtime"
    INSPECT_STATUS = "inspect_status"


class UserActionType(StrEnum):
    """需要用户介入时暴露给上层的动作类型。"""

    LOGIN_OR_VERIFICATION = "login_or_verification"
    CAPTCHA = "captcha"
    CONFIRM_HIGH_RISK_ACTION = "confirm_high_risk_action"
    ENTER_SECRET_MANUALLY = "enter_secret_manually"


class InterventionSignalType(StrEnum):
    """用户介入检测器产生的信号类型。"""

    AUTH_PAGE = "auth_page"
    CAPTCHA = "captcha"
    HIGH_RISK_ACTION = "high_risk_action"
    SECRET_INPUT = "secret_input"


class SnapshotMode(StrEnum):
    """页面快照模式。"""

    FULL = "full"
    FOCUSED = "focused"


class ActionStatus(StrEnum):
    """ActionResult.status 的稳定枚举。"""

    COMPLETED = "completed"


class RuntimeProvider(StrEnum):
    """浏览器运行时提供方。"""

    LOCAL_PLAYWRIGHT = "local_playwright"


class BrowserEngine(StrEnum):
    """浏览器引擎。"""

    CHROMIUM = "chromium"


class BrowserDialogType(StrEnum):
    """Playwright dialog 类型。"""

    ALERT = "alert"
    BEFORE_UNLOAD = "beforeunload"
    CONFIRM = "confirm"
    PROMPT = "prompt"


class RuntimeMode(StrEnum):
    """浏览器显示模式。"""

    HEADED = "headed"
    HEADLESS = "headless"


class ScrollDirection(StrEnum):
    """滚动方向。"""

    UP = "up"
    DOWN = "down"
    LEFT = "left"
    RIGHT = "right"


class KeyboardKey(StrEnum):
    """Playwright keyboard key 名称。"""

    ALT = "Alt"
    ARROW_DOWN = "ArrowDown"
    ARROW_LEFT = "ArrowLeft"
    ARROW_RIGHT = "ArrowRight"
    ARROW_UP = "ArrowUp"
    BACKSPACE = "Backspace"
    CONTROL = "Control"
    DELETE = "Delete"
    END = "End"
    ENTER = "Enter"
    ESCAPE = "Escape"
    HOME = "Home"
    META = "Meta"
    PAGE_DOWN = "PageDown"
    PAGE_UP = "PageUp"
    SHIFT = "Shift"
    SPACE = " "
    TAB = "Tab"


class WaitUntil(StrEnum):
    """Playwright 页面导航等待策略。"""

    DOM_CONTENT_LOADED = "domcontentloaded"
    LOAD = "load"
    NETWORK_IDLE = "networkidle"


class WaitForState(StrEnum):
    """等待页面元素或文本时支持的稳定状态。"""

    ATTACHED = "attached"
    DETACHED = "detached"
    VISIBLE = "visible"
    HIDDEN = "hidden"


class ControllerLogStage(StrEnum):
    """controller 在无法解析有效 action type 时使用的性能日志阶段。"""

    INVALID_ACTION = "invalid_action"
    UNKNOWN_ACTION = "unknown_action"


class BrowserToolName(StrEnum):
    """Browser 生态内稳定工具名。"""

    BROWSE_INTERACT = "browse_interact"


class ContentTrustLevel(StrEnum):
    """页面内容可信边界标签。"""

    UNTRUSTED_PAGE_CONTENT = "untrusted_page_content"
