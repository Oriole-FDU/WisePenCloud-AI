from typing import Tuple

_BLOCKED_PAGE_TEXT_MARKERS: Tuple[str, ...] = (
    # 机器人挑战页特征。
    "just a moment",
    "checking your browser",
    "verify you are human",
    "verifying you are human",
    "are you a robot",
    "are you human",
    "请完成验证",
    "安全验证",
    # 验证码页特征。
    "complete the captcha",
    "captcha verification",
    "captcha required",
    "请输入验证码",
    "完成验证码",
    "人机验证",
    # 依赖 JavaScript 才能继续访问的页面特征。
    "please enable javascript",
    "enable javascript to continue",
    "javascript is required",
    "requires javascript",
    "请开启 javascript",
    "请启用 javascript",
    "请开启javascript",
    "请启用javascript",
    # 访问拒绝或被拦截的页面特征。
    "access denied",
    "request blocked",
    "you have been blocked",
    "temporarily blocked",
    "unusual traffic",
    "访问受限",
    "访问被拒绝",
    "请求被拦截",
    # 频率限制页面特征。
    "too many requests",
    "rate limit exceeded",
    "rate limited",
    "访问过于频繁",
    "请求过于频繁",
    # 需要登录后访问的页面特征。
    "please sign in",
    "please log in",
    "login required",
    "sign in to continue",
    "log in to continue",
    "需要登录",
    "请登录",
    "登录后查看",
    "请先登录",
    # HTTP 错误页面特征。
    "400 bad request",
    "401 unauthorized",
    "403 forbidden",
    "404 not found",
    "405 method not allowed",
    "408 request timeout",
    "410 gone",
    "451 unavailable for legal reasons",
    "500 internal server error",
    "502 bad gateway",
    "503 service unavailable",
    "504 gateway timeout",
    "page not found",
    "bad gateway",
    "service unavailable",
    "internal server error",
    "页面不存在",
    "网页不存在",
    "未找到页面",
    "服务不可用",
    "服务器错误",
)


def looks_like_blocked_page(text: str) -> bool:
    """检测文本是否包含反爬、验证码或错误页面等特征关键词。

    取前 30000 个字符进行小写匹配，命中任一特征标记即判定为受限页面。
    """
    sample = text[:30000].lower()
    return any(marker in sample for marker in _BLOCKED_PAGE_TEXT_MARKERS)