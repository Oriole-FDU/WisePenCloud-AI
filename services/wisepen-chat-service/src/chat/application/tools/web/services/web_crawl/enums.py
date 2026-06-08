from enum import StrEnum


class CrawlItemKind(StrEnum):
    """爬取结果条目类型枚举，标识条目是页面、文档、跳过或错误。"""
    PAGE = "page"
    DOCUMENT = "document"
    SKIPPED = "skipped"
    ERROR = "error"


class CrawlSkipReason(StrEnum):
    """URL 被跳过的原因枚举，覆盖安全、协议、预算、反爬等各维度。"""
    NON_URL_REFERENCE = "non_url_reference"
    URL_SECURITY_REJECTED = "url_security_rejected"
    ROBOTS_DISALLOWED = "robots_disallowed"
    ROBOTS_UNAVAILABLE = "robots_unavailable"
    DEPTH_LIMIT = "depth_limit"
    PAGE_LIMIT = "page_limit"
    EXTERNAL_BUDGET_EXCEEDED = "external_budget_exceeded"
    EXTERNAL_HOST_BUDGET_EXCEEDED = "external_host_budget_exceeded"
    EXTERNAL_DEPTH_LIMIT = "external_depth_limit"
    UNSUPPORTED_SCHEME = "unsupported_scheme"
    UNSUPPORTED_MEDIA = "unsupported_media"
    BLOCKED_PATH = "blocked_path"
    LOGIN_REQUIRED = "login_required"
    PERMISSION_DENIED = "permission_denied"
    PAYWALL_DETECTED = "paywall_detected"
    CAPTCHA_DETECTED = "captcha_detected"
    BOT_CHALLENGE = "bot_challenge"
    JS_REQUIRED = "js_required"
    SPA_SHELL = "spa_shell"
    RATE_LIMITED = "rate_limited"
    LOW_RELEVANCE = "low_relevance"
    DUPLICATE_URL = "duplicate_url"
    FETCH_FAILED = "fetch_failed"


class RobotsDecisionReason(StrEnum):
    """Robots.txt 决策原因枚举。"""
    UNAVAILABLE_SEED_ALLOWED = "robots_unavailable_seed_allowed"
    UNAVAILABLE = "robots_unavailable"
    DISALLOWED = "robots_disallowed"