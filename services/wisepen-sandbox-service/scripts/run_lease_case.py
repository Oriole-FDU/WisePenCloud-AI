from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from _sandbox_http import SandboxHttp, execute


DEFAULT_STATE_FILE = "/tmp/wisepen-sandbox-e2e-lease.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Sandbox lease and execute checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9210")
    parser.add_argument("--source", default="APISIX-wX0iR6tY")
    parser.add_argument("--tenant", default="tenant-a")
    parser.add_argument("--workspace", default="workspace-a")
    parser.add_argument("--request-id", default="mcp:tenant-a:workspace-a")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument("--ready-timeout", type=float, default=120.0)
    return parser.parse_args()


def wait_ready(api: SandboxHttp, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = api.client.get("/readyz")
            if response.status_code == 200:
                print(f"readyz=PASS {response.text}")
                return
            print(f"readyz=WAIT HTTP {response.status_code}")
        except Exception as exc:
            print(f"readyz=WAIT {type(exc).__name__}: {exc}")
        time.sleep(1)
    raise AssertionError(f"readyz did not become ready within {timeout}s")


def allocate(
    api: SandboxHttp, request_id: str, tenant: str, workspace: str
) -> dict[str, Any]:
    response, body = api.json_request(
        "POST",
        "/internal/sandboxes/allocate",
        json={
            "request_id": request_id,
            "tenant_id": tenant,
            "workspace_id": workspace,
        },
    )
    return api.require_success("allocate", response, body)


def main() -> int:
    args = parse_args()
    api = SandboxHttp(args.base_url, args.source)
    try:
        wait_ready(api, args.ready_timeout)

        lease = allocate(api, args.request_id, args.tenant, args.workspace)
        lease["tenant_id"] = args.tenant
        lease["workspace_id"] = args.workspace
        print(
            "allocate=PASS "
            f"lease_id={lease['lease_id']} sandbox_id={lease['sandbox_id']} "
            f"fencing_token={lease['fencing_token']}"
        )
        state = {
            "base_url": args.base_url,
            "source": args.source,
            "request_id": args.request_id,
            "tenant_id": args.tenant,
            "workspace_id": args.workspace,
            "lease_id": lease["lease_id"],
            "sandbox_id": lease["sandbox_id"],
            "fencing_token": lease["fencing_token"],
            "status": "running",
        }
        Path(args.state_file).write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
        print(f"state=PASS file={args.state_file}")

        same_lease = allocate(api, args.request_id, args.tenant, args.workspace)
        if (
            same_lease["lease_id"] != lease["lease_id"]
            or same_lease["sandbox_id"] != lease["sandbox_id"]
        ):
            raise AssertionError(
                f"idempotent allocate returned a different lease: {same_lease}"
            )
        print("allocate_idempotency=PASS")

        write_data = execute(
            api,
            lease,
            "tool-write-001",
            "write_file",
            {"file": "probe.txt", "content": "cached-value"},
        )
        print(f"write_file=PASS data={write_data}")

        read_data = execute(
            api, lease, "tool-read-001", "read_file", {"file": "probe.txt"}
        )
        if "cached-value" not in json.dumps(read_data, ensure_ascii=False):
            raise AssertionError(f"read_file did not return cached-value: {read_data}")
        print("read_file=PASS content=cached-value")

        shell_data = execute(
            api,
            lease,
            "tool-shell-001",
            "shell_exec",
            {
                "command": "printf wisepen-e2e",
                "exec_dir": ".",
                "timeout_ms": 30000,
            },
        )
        if "wisepen-e2e" not in json.dumps(shell_data, ensure_ascii=False):
            raise AssertionError(f"shell_exec returned unexpected data: {shell_data}")
        print("shell_exec=PASS")

        code_data = execute(
            api,
            lease,
            "tool-code-001",
            "execute",
            {"language": "python", "code": 'print("wisepen-e2e")'},
        )
        if "wisepen-e2e" not in json.dumps(code_data, ensure_ascii=False):
            raise AssertionError(
                f"code execute returned unexpected data: {code_data}"
            )
        print("code_execute=PASS")

        response, fencing_body = api.json_request(
            "POST",
            f"/internal/leases/{lease['lease_id']}/execute",
            json={
                "request_id": "tool-invalid-fencing",
                "tenant_id": args.tenant,
                "workspace_id": args.workspace,
                "fencing_token": 999999,
                "operation": "shell_exec",
                "payload": {"command": "echo should-not-run"},
            },
        )
        if (
            response.status_code != 200
            or fencing_body.get("code") != 46004
            or fencing_body.get("data") is not None
        ):
            raise AssertionError(
                f"fencing rejection failed: {response.status_code} {fencing_body}"
            )
        print("fencing_rejection=PASS code=46004")

        print("lease_kept=PASS; run run_vnc_case.py before releasing this lease")
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
