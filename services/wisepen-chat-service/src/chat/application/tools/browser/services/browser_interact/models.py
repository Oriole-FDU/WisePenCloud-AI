from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from chat.application.tools.browser.services.browser_interact.enums import (
    BrowserEngine,
    RuntimeProvider,
)


@dataclass(frozen=True, slots=True)
class PageState:
    """当前浏览器页面的可观测状态。"""

    url: str
    title: str
    ready_state: Optional[str]
    is_closed: bool


@dataclass(frozen=True, slots=True)
class SessionState:
    """浏览器会话状态，随每次工具响应返回。"""

    browser_session_id: Optional[str]
    valid: bool
    created: bool = False
    reused: bool = False


@dataclass(frozen=True, slots=True)
class BrowserRuntimeState:
    """浏览器运行时形态，区分本地、沙箱、引擎和显示模式。"""

    provider: str
    engine: str
    sandboxed: bool
    mode: str


@dataclass(frozen=True, slots=True)
class BrowserEvent:
    """浏览器运行时事件摘要，用于 status 观测。"""

    type: str
    message: str
    page_url: Optional[str] = None


@dataclass(frozen=True, slots=True)
class BrowserLaunchOptions:
    """浏览器启动选项。

    Args:
        timeout: Playwright 启动超时时间，单位秒。
        headless: 是否以 headless 模式启动。
        disable_sandbox: 是否追加 `--no-sandbox`，用于受限容器环境。
        disable_dev_shm_usage: 是否追加 `--disable-dev-shm-usage`，用于小 /dev/shm 容器。
        runtime_provider: 运行时提供方标识。
        runtime_engine: 浏览器引擎标识。
    """

    timeout: int = 30
    headless: bool = False
    disable_sandbox: bool = False
    disable_dev_shm_usage: bool = False
    runtime_provider: str = RuntimeProvider.LOCAL_PLAYWRIGHT.value
    runtime_engine: str = BrowserEngine.CHROMIUM.value


@dataclass(frozen=True, slots=True)
class RecoveryHint:
    """调用方重试或恢复当前错误时需要执行的动作提示。"""

    type: str
    scope: str = "tool_state"
    required_before_retry: bool = False
    reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class UserActionRequest:
    """需要用户在可见浏览器中完成的介入动作。"""

    type: str
    message: str
    detected_reason: Optional[str] = None


@dataclass(frozen=True, slots=True)
class ToolError:
    """browse_interact 对外暴露的结构化错误信息。"""

    category: str
    code: str
    message: str
    retryable: bool
    requires_user_action: bool = False
    user_action: Optional[UserActionRequest] = None
    recovery_hint: Optional[RecoveryHint] = None
    context: Dict[str, Any] = field(default_factory=dict)
    diagnostic_code: Optional[str] = None


@dataclass(frozen=True, slots=True)
class SnapshotPayload:
    """一次页面快照的树形文本与 ref 元数据。"""

    snapshot_id: str
    tree: str
    refs_valid_for: str = "current_dom_only"
    mode: str = "full"
    goal: Optional[str] = None
    scope_ref: Optional[str] = None
    refs: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class InterventionSignal:
    """页面状态中识别出的用户介入信号。"""

    type: str
    confidence: float
    reason: str
    evidence: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ActionResult:
    """单次浏览器 action 的执行摘要。"""

    type: str
    status: str
    detail: Dict[str, Any]
