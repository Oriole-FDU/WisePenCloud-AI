"""
基于 Nacos ServiceDiscovery 的通用内部 RPC 客户端
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

import httpx

from common.cloud.service_discovery import ServiceDiscovery, LoadBalancingStrategy
from common.core.constants import SecurityConstants
from common.core.exceptions import RpcError, ServiceUnavailableError
from common.logger import log_error, log_fail

# Java 端 R<T> 的成功 code；与 ResultCode.SUCCESS 对齐（200）
_R_SUCCESS_CODE = 200


class RpcClient:
    """
    RPC 客户端，用于发起内部服务的 HTTP 调用
    """

    def __init__(
        self,
        discovery: ServiceDiscovery,
        *,
        from_source_secret: str,
        timeout: float = 5.0,
        retries: int = 2,
        default_strategy: Optional[LoadBalancingStrategy] = None,
        limits: Optional[httpx.Limits] = None,
    ) -> None:
        self._discovery = discovery
        self._from_source_secret = from_source_secret
        self._timeout = timeout
        self._retries = max(0, int(retries))
        self._strategy = default_strategy
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout),
            limits=limits or httpx.Limits(max_keepalive_connections=32, max_connections=128),
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------- 便捷方法 ----------

    async def get(self, service_name: str, path: str, **kwargs: Any) -> Any:
        return await self.request("GET", service_name, path, **kwargs)

    async def post(self, service_name: str, path: str, **kwargs: Any) -> Any:
        return await self.request("POST", service_name, path, **kwargs)

    async def delete(self, service_name: str, path: str, **kwargs: Any) -> Any:
        return await self.request("DELETE", service_name, path, **kwargs)

    # ---------- 核心请求 ----------

    async def request(
        self,
        method: str,
        service_name: str,
        path: str,
        *,
        params: Optional[Mapping[str, Any]] = None,
        json: Any = None,
        headers: Optional[Mapping[str, str]] = None,
        timeout: Optional[float] = None,
    ) -> Any:
        """
        发起到内部服务的 HTTP 调用并解包
        """
        attempts = self._retries + 1
        tried_instances: set[str] = set()

        last_status: Optional[int] = None
        last_code: Optional[int] = None
        last_msg: Optional[str] = None
        last_exc: Optional[BaseException] = None

        merged_headers: Dict[str, str] = {
            SecurityConstants.HEADER_FROM_SOURCE: self._from_source_secret,
        }
        if headers:
            merged_headers.update({k: v for k, v in headers.items()})

        req_timeout = httpx.Timeout(timeout) if timeout is not None else None

        for attempt in range(attempts):
            try:
                instance = await self._discovery.pick(
                    service_name, strategy=self._strategy, exclude=tried_instances
                )
            except ServiceUnavailableError as e:
                last_exc = e
                break

            addr = f"{instance.ip}:{instance.port}"
            tried_instances.add(addr)
            url = f"http://{addr}{path}"

            try:
                resp = await self._client.request(
                    method.upper(),
                    url,
                    params=params,
                    json=json,
                    headers=merged_headers,
                    timeout=req_timeout,
                )
                last_status = resp.status_code

                if resp.status_code >= 500:
                    last_msg = f"upstream 5xx: {resp.text[:200]}"
                    log_fail(
                        "RPC upstream 5xx",
                        last_msg,
                        service=service_name,
                        path=path,
                        addr=addr,
                        status=resp.status_code,
                        attempt=attempt + 1,
                    )
                    continue  # 5xx 换实例重试

                # 非 5xx 就尝试解 R<T>；4xx / 200 失败都直接不重试
                try:
                    body = resp.json()
                except Exception as e:
                    last_msg = f"non-json body: {resp.text[:200]}"
                    last_exc = e
                    break

                if not isinstance(body, dict) or "code" not in body:
                    last_msg = "response is not R<T> shape"
                    break

                last_code = int(body.get("code"))
                last_msg = body.get("msg")
                if last_code == _R_SUCCESS_CODE:
                    return body.get("data")

                # 业务错误不做跨实例重试
                break

            except (httpx.ConnectError, httpx.ConnectTimeout, httpx.ReadTimeout, httpx.RemoteProtocolError) as e:
                last_exc = e
                last_msg = f"{type(e).__name__}: {e}"
                log_fail(
                    "RPC 网络异常",
                    last_msg,
                    service=service_name,
                    path=path,
                    addr=addr,
                    attempt=attempt + 1,
                )
                continue
            except Exception as e:
                last_exc = e
                last_msg = f"{type(e).__name__}: {e}"
                log_error("RPC 未预期异常", e, service=service_name, path=path, addr=addr)
                break

        raise RpcError(
            service_name=service_name,
            path=path,
            status=last_status,
            code=last_code,
            msg=last_msg,
            cause=last_exc,
        )
