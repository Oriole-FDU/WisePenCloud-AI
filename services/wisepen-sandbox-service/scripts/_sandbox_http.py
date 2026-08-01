from __future__ import annotations

from typing import Any

import httpx


class SandboxHttp:
    def __init__(self, base_url: str, source: str) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"X-From-Source": source},
            timeout=30.0,
            follow_redirects=False,
            trust_env=False,
        )

    def close(self) -> None:
        self.client.close()

    def json_request(
        self, method: str, path: str, **kwargs: Any
    ) -> tuple[httpx.Response, dict[str, Any]]:
        response = self.client.request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise AssertionError(
                f"{method} {path} returned non-JSON: HTTP "
                f"{response.status_code} {response.text[:300]}"
            ) from exc
        if not isinstance(payload, dict):
            raise AssertionError(f"{method} {path} returned a non-object JSON payload")
        return response, payload

    @staticmethod
    def require_success(
        path: str, response: httpx.Response, payload: dict[str, Any]
    ) -> dict[str, Any]:
        if response.status_code != 200 or payload.get("code") != 200:
            raise AssertionError(
                f"{path} failed: HTTP {response.status_code}, payload={payload}"
            )
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AssertionError(f"{path} returned no object data: {payload}")
        return data


def execute(
    api: SandboxHttp,
    lease: dict[str, Any],
    request_id: str,
    operation: str,
    payload: dict[str, Any],
    *,
    fencing_token: int | None = None,
) -> dict[str, Any]:
    response, body = api.json_request(
        "POST",
        f"/internal/leases/{lease['lease_id']}/execute",
        json={
            "request_id": request_id,
            "tenant_id": lease["tenant_id"],
            "workspace_id": lease["workspace_id"],
            "fencing_token": lease["fencing_token"]
            if fencing_token is None
            else fencing_token,
            "operation": operation,
            "payload": payload,
        },
    )
    return api.require_success("execute", response, body)
