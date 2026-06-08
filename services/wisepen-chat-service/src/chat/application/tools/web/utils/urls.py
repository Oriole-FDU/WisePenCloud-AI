import re
from typing import Optional
from urllib.parse import urljoin, urlparse, urlencode, parse_qsl, urlunparse

TRACKING_PARAMS = {
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
    "utm_id", "fbclid", "gclid", "msclkid", "ref", "spm",
}
MULTI_SLASH_RE = re.compile(r"/+")


def canonicalize_url(url: str, *, base_url: Optional[str] = None) -> str:
    """
    URL 规范化：抹除协议大小写变体、WWW前缀、冗余斜杠、尾部斩断、
    以及广告追踪参数，将任意混乱网页链接归一化为全网唯一标准身份证。

    Args:
    - urls: 网页抓取到或下游解析出的原始混乱链接
    - base_url: 可选的相对路径基准站源 URL（用于自动补全相对路径）

    Return:
    - str: 具备唯一确定性的标准化规范化 URL 字符串
    """
    raw_url = url.strip() if url else ""
    if base_url:
        raw_url = urljoin(base_url, raw_url)

    parsed = urlparse(raw_url)
    scheme = parsed.scheme.lower() or "https"
    netloc = parsed.netloc.lower().removeprefix("www.")
    path = MULTI_SLASH_RE.sub("/", parsed.path or "/").rstrip("/") or "/"

    # 利用生成器一步完成参数清洗、语种过滤、字典正向排序与 URL 编码序列化。
    query = urlencode(
        sorted(
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=False)
            if k.lower() not in TRACKING_PARAMS
        ),
        doseq=True,
    )

    return urlunparse((scheme, netloc, path, "", query, ""))
