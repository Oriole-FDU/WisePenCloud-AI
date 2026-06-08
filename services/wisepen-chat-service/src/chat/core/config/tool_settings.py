from typing import Optional

from pydantic import BaseModel


class ToolSettings(BaseModel):
    """Tool 与容器层面的行为配置，与基础设施解耦。

    本类存放 Tool 构造器 / 容器 Provider 所需的运行时参数（超时、并发、
    工具专用 API Key、User-Agent 等）。Base URL 等全局基础设施配置
    仍在 app_settings.Settings 中。
    """

    # ── Web Search ────────────────────────────────────────────────
    WEB_SEARCH_USER_AGENT: str = (
        "WisePenCloud-AI web_search/1.0 (contact: jzsun24@m.fudan.edu.cn)"
    )
    FOURGET_WEB_SCRAPER: str = "ddg"
    FOURGET_TIMEOUT: float = 8.0
    FOURGET_MAX_CONCURRENCY: int = 5
    SERPER_API_KEY: Optional[str] = (
        "ce1fb242cbfc5dd0097d97f0ea866bf34f571ba3"
    )

    # ── Math Solver ───────────────────────────────────────────────
    SAGE_MATH_WORKER_TIMEOUT_SECONDS: int | float = 10

    # ── Browser Interact ──────────────────────────────────────────
    BROWSER_INTERACT_TIMEOUT_SECONDS: int = 30
    BROWSER_INTERACT_HEADLESS: bool = False
    BROWSER_INTERACT_DISABLE_SANDBOX: bool = False
    BROWSER_INTERACT_DISABLE_DEV_SHM_USAGE: bool = False

    # ── Search Provider Credential ────────────────────────────────
    SEARCH_PROVIDER_CREDENTIAL_MASTER_KEY: str = "ivdklTUVOL7MpIqEhgzwxRJx3tDXBhmvqUmWB4sXc_s="
    SEARCH_PROVIDER_CREDENTIAL_KEY_ID: str = "local-dev-v2"

    # ── Web Fetch ─────────────────────────────────────────────────
    WEB_ACCESS_DOH_SERVERS: tuple[str, ...] = (
        "https://dns.alidns.com/dns-query",
        "https://doh.pub/dns-query",
        "https://cloudflare-dns.com/dns-query",
        "https://dns.google/dns-query",
    )
    STEEL_TIMEOUT: float = 30.0
    STEEL_DELAY_MS: float = 2000.0
    STEEL_CONCURRENCY: int = 3
    STATIC_FETCH_MAX_RESPONSE_BYTES: int = 52_428_800
    LOCAL_SCRIPT_TIMEOUT: float = 30.0
    LOCAL_SCRIPT_WORKER_COUNT: int = 5
    LOCAL_SCRIPT_RESTART_AFTER: int = 200

    # ── Fetch Coordinator ─────────────────────────────────────────
    FETCH_MIN_CONTENT_LENGTH: int = 400
    FETCH_LAST_RESORT_MIN_LENGTH: int = 50
    FETCH_CACHE_TTL_SECONDS: int = 600
    FETCH_CACHE_MAX_ITEMS: int = 128



tool_settings = ToolSettings()
