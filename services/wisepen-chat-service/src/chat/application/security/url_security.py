import socket
from ipaddress import ip_address, ip_network
from typing import List
from urllib.parse import urlparse

import dns.message
import dns.query
import dns.rdatatype

from chat.core.config.tool_settings import tool_settings
from common.logger import log_event, log_fail


class UrlSecurityError(Exception):
    pass


# SSRF 核心拦截网段：
# - 明确内网 / 本机 / link-local / multicast / documentation / reserved。
# - 198.18.0.0/15 不作为普通 blocked IP 直接拦截；
#   它常见于 Clash / sing-box / mihomo fake-ip 模式。
_BLOCKED_IP_NETWORKS = tuple(
    ip_network(network)
    for network in (
        "0.0.0.0/8",
        "10.0.0.0/8",
        "100.64.0.0/10",
        "127.0.0.0/8",
        "169.254.0.0/16",
        "172.16.0.0/12",
        "192.0.0.0/24",
        "192.0.2.0/24",
        "192.168.0.0/16",
        "198.51.100.0/24",
        "203.0.113.0/24",
        "224.0.0.0/4",
        "240.0.0.0/4",
        "::/128",
        "::1/128",
        "::ffff:0:0/96",
        "64:ff9b::/96",
        "100::/64",
        "2001::/23",
        "2001:db8::/32",
        "fc00::/7",
        "fe80::/10",
        "ff00::/8",
    )
)

_FAKE_IP_NETWORKS = (
    ip_network("198.18.0.0/15"),
)

_DOH_RECORD_TYPES = (
    dns.rdatatype.A,
    dns.rdatatype.AAAA,
)

_DOH_TIMEOUT_SECONDS = 5.0

_BLOCKED_HOSTNAMES = {
    "localhost",
    "localhost.localdomain",
}

_BLOCKED_HOST_SUFFIXES = (
    ".localhost",
    ".local",
)


def _is_fake_ip(value: str) -> bool:
    try:
        ip = ip_address(value)
    except ValueError:
        return False

    return any(ip in network for network in _FAKE_IP_NETWORKS)


def _is_blocked_ip(value: str) -> bool:
    try:
        ip = ip_address(value)
    except ValueError:
        return False

    if _is_fake_ip(value):
        return False

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        return True

    return any(ip in network for network in _BLOCKED_IP_NETWORKS)


def _validate_hostname(hostname: str) -> str:
    normalized = hostname.strip().strip(".").lower()

    if not normalized:
        raise UrlSecurityError("URL is missing a hostname")

    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in normalized):
        raise UrlSecurityError("URL hostname contains invalid characters")

    if normalized in _BLOCKED_HOSTNAMES or normalized.endswith(_BLOCKED_HOST_SUFFIXES):
        raise UrlSecurityError("Hostname is blocked")

    return normalized


def _resolve_with_system_dns(hostname: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        log_fail(
            "URL system DNS 查询",
            f"hostname={hostname}, error={e}",
        )
        return []

    return sorted({info[4][0] for info in infos})


def _resolve_with_doh(hostname: str) -> List[str]:
    for doh_url in tool_settings.WEB_ACCESS_DOH_SERVERS:
        ips: List[str] = []

        for record_type in _DOH_RECORD_TYPES:
            try:
                query = dns.message.make_query(hostname, record_type)
                response = dns.query.https(
                    query,
                    doh_url,
                    timeout=_DOH_TIMEOUT_SECONDS,
                )
            except Exception as e:
                log_fail(
                    "DoH 查询",
                    f"hostname={hostname}, doh={doh_url}, type={record_type}, error={e}",
                )
                continue

            for rrset in response.answer:
                for item in rrset:
                    value = str(item)
                    try:
                        ip_address(value)
                    except ValueError:
                        continue

                    ips.append(value)

        if ips:
            return sorted(set(ips))

    return []


def _raise_if_any_blocked_ip(ips: List[str], *, source: str) -> None:
    blocked_ip = next((value for value in ips if _is_blocked_ip(value)), "")
    if blocked_ip:
        raise UrlSecurityError(
            f"Hostname resolves to a blocked IP address through {source}: {blocked_ip}"
        )


def _validate_public_host_best_effort(hostname: str) -> None:
    normalized = _validate_hostname(hostname)

    try:
        literal_ip = ip_address(normalized)
    except ValueError:
        literal_ip = None

    # IP 字面量必须严格校验。
    # 这种场景没有 DNS / 代理 fake-ip 误判空间。
    if literal_ip is not None:
        if _is_blocked_ip(str(literal_ip)) or _is_fake_ip(str(literal_ip)):
            raise UrlSecurityError("IP address is blocked")

        return

    system_ips = _resolve_with_system_dns(normalized)

    # 系统 DNS 失败时，不在 validator 阶段直接拦截。
    # 原因：
    # - 实际 fetcher 可能走代理、浏览器、容器内 DNS 或第三方 runtime。
    # - validator 只负责拦截明确 SSRF 风险，不负责替 fetcher 证明可达性。
    if not system_ips:
        doh_ips = _resolve_with_doh(normalized)
        if doh_ips:
            _raise_if_any_blocked_ip(doh_ips, source="DoH")
            return

        log_event(
            "URL DNS 未解析但放行",
            hostname=normalized,
            reason="system_dns_and_doh_failed",
        )
        return

    real_system_ips = [
        value for value in system_ips
        if not _is_fake_ip(value)
    ]

    # 全部都是 fake-ip：用 DoH 尝试确认真实地址。
    # DoH 成功则按真实地址拦截内网；DoH 失败则放行，避免代理 fake-ip 环境误杀公网 URL。
    if not real_system_ips:
        doh_ips = _resolve_with_doh(normalized)
        if doh_ips:
            _raise_if_any_blocked_ip(doh_ips, source="DoH")
            return

        log_event(
            "URL fake-ip DNS 放行",
            hostname=normalized,
            system_ips=system_ips,
            reason="all_system_ips_are_fake_ip_and_doh_failed",
        )
        return

    _raise_if_any_blocked_ip(real_system_ips, source="system DNS")


def validate_public_http_url(url: str) -> str:
    if not url:
        raise UrlSecurityError("URL is empty")

    if url != url.strip() or any(
        ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url
    ):
        raise UrlSecurityError("URL contains invalid characters")

    if "\\" in url:
        raise UrlSecurityError("URL cannot contain backslashes")

    parsed = urlparse(url)

    if parsed.scheme not in {"http", "https"}:
        raise UrlSecurityError("URL scheme must be http or https")

    if not parsed.netloc or not parsed.hostname:
        raise UrlSecurityError("URL is missing a hostname")

    if parsed.username or parsed.password:
        raise UrlSecurityError("URL cannot contain userinfo")

    try:
        port = parsed.port
    except ValueError as e:
        raise UrlSecurityError("URL port is invalid") from e

    if port is not None and (port <= 0 or port > 65535):
        raise UrlSecurityError("URL port is invalid")

    _validate_public_host_best_effort(parsed.hostname)

    return url