from __future__ import annotations

from urllib.parse import urlparse


def normalize_domain(value: str) -> str:
    """
    通用域名清洗规范化算子。
    将输入的任意原始字符串（包含大模型产生的各种脏输入）统一物理洗净为标准 bare domain（如 'github.com'）。
    如果输入包含非法通配符、非法空格或无法解析，则返回空字符串。
    """
    candidate = value.strip().lower().rstrip(".")
    if not candidate:
        return ""

    # 识别并提取主机名核心。
    if "://" in candidate:
        host = urlparse(candidate).hostname or ""
    else:
        # 防御：对于无协议头的输入，如果包含路径/查询参数/锚点，说明不是纯域名输入，属于非法脏数据
        if "/" in candidate or "?" in candidate or "#" in candidate:
            return ""
        host = candidate

    # 物理除杂与合法性校验
    host = host.lower().rstrip(".")
    if not host or "*" in host or any(char.isspace() for char in host):
        return ""

    return host.removeprefix("www.")


def extract_domain(url: str) -> str:
    """
    从完整的 URL 链接中提取出洗净后的裸域名（Bare Domain）。
    """
    if url and "://" not in url and "/" in url:
        # 处理类似 'github.com/trending' 的特殊脏输入边界
        url = f"http://{url}"

    domain = urlparse(url).hostname if url else ""
    if not domain:
        return ""

    return domain.lower().removeprefix("www.")
