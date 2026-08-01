from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from _sandbox_http import SandboxHttp, execute


DEFAULT_STATE_FILE = "/tmp/wisepen-sandbox-e2e-lease.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Reuse the lease from run_lease_case.py in VNC."
    )
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE)
    parser.add_argument(
        "--no-pause",
        action="store_true",
        help="Do not wait for manual VNC inspection.",
    )
    return parser.parse_args()


def load_state(path: str) -> dict[str, Any]:
    state = json.loads(Path(path).read_text(encoding="utf-8"))
    required = {
        "base_url",
        "source",
        "tenant_id",
        "workspace_id",
        "lease_id",
        "sandbox_id",
        "fencing_token",
    }
    missing = sorted(required - set(state))
    if missing:
        raise AssertionError(f"state file is missing fields: {missing}")
    if state.get("status") != "running":
        raise AssertionError(f"state is not running: {state}")
    return state


def pause(message: str, no_pause: bool) -> None:
    if no_pause:
        return
    if not sys.stdin.isatty():
        print("stdin is not interactive; continuing without pause")
        return
    input(message)


def main() -> int:
    args = parse_args()
    state = load_state(args.state_file)
    api = SandboxHttp(state["base_url"], state["source"])
    vnc_headers = {
        "X-User-Id": state["tenant_id"],
        "X-Session-Id": state["workspace_id"],
    }
    lease = {
        "lease_id": state["lease_id"],
        "sandbox_id": state["sandbox_id"],
        "tenant_id": state["tenant_id"],
        "workspace_id": state["workspace_id"],
        "fencing_token": state["fencing_token"],
    }
    try:
        _, before = api.json_request("GET", "/v1/sandbox/gateway/vnc/status")
        print(f"vnc_status_before=PASS {before}")

        response = api.client.get(
            "/v1/sandbox/gateway/vnc",
            headers=vnc_headers,
        )
        if response.status_code != 302:
            raise AssertionError(f"VNC did not redirect: HTTP {response.status_code}")
        vnc_url = response.headers.get("location")
        if not vnc_url:
            raise AssertionError("VNC redirect has no Location header")
        print(f"vnc_open=PASS url={vnc_url}")

        _, after = api.json_request("GET", "/v1/sandbox/gateway/vnc/status")
        binding_key = f"{state['tenant_id']}:{state['workspace_id']}"
        actual_sandbox_id = after.get("bindings", {}).get(binding_key)
        if actual_sandbox_id != state["sandbox_id"]:
            raise AssertionError(
                "VNC bound to a different sandbox: "
                f"expected={state['sandbox_id']} actual={actual_sandbox_id}"
            )
        print(f"vnc_same_sandbox=PASS sandbox_id={actual_sandbox_id}")

        execute(
            api,
            lease,
            "tool-vnc-live-001",
            "write_file",
            {"file": "probe.txt", "content": "vnc-live-001"},
        )
        read_data = execute(
            api, lease, "tool-vnc-read-001", "read_file", {"file": "probe.txt"}
        )
        if "vnc-live-001" not in json.dumps(read_data, ensure_ascii=False):
            raise AssertionError(
                f"first VNC file update was not readable: {read_data}"
            )
        print("vnc_file_update_1=PASS content=vnc-live-001")
        print(
            "Open this URL in a browser and inspect "
            f"/home/gem/{state['tenant_id']}/{state['workspace_id']}/probe.txt:\n"
            f"{vnc_url}"
        )
        pause(
            "Press Enter after confirming the first value in VNC... ",
            args.no_pause,
        )

        execute(
            api,
            lease,
            "tool-vnc-live-002",
            "write_file",
            {"file": "probe.txt", "content": "vnc-live-002"},
        )
        read_data = execute(
            api, lease, "tool-vnc-read-002", "read_file", {"file": "probe.txt"}
        )
        if "vnc-live-002" not in json.dumps(read_data, ensure_ascii=False):
            raise AssertionError(
                f"second VNC file update was not readable: {read_data}"
            )
        print("vnc_file_update_2=PASS content=vnc-live-002")
        pause(
            "Refresh the VNC directory, confirm vnc-live-002, then press Enter... ",
            args.no_pause,
        )

        response, release_body = api.json_request(
            "POST",
            "/v1/sandbox/gateway/vnc/release",
            headers=vnc_headers,
        )
        if response.status_code != 200 or release_body.get("status") != "released":
            raise AssertionError(
                f"VNC release failed: {response.status_code} {release_body}"
            )
        print("vnc_release=PASS")

        response, repeat_body = api.json_request(
            "POST",
            "/v1/sandbox/gateway/vnc/release",
            headers=vnc_headers,
        )
        if response.status_code != 200 or repeat_body.get("status") != "released":
            raise AssertionError(
                f"repeat VNC release failed: {response.status_code} {repeat_body}"
            )
        print("vnc_release_idempotency=PASS")

        response, stale_body = api.json_request(
            "POST",
            f"/internal/leases/{lease['lease_id']}/execute",
            json={
                "request_id": "tool-after-vnc-release",
                "tenant_id": lease["tenant_id"],
                "workspace_id": lease["workspace_id"],
                "fencing_token": lease["fencing_token"],
                "operation": "read_file",
                "payload": {"file": "probe.txt"},
            },
        )
        if (
            response.status_code != 200
            or stale_body.get("code") == 200
            or stale_body.get("data") is not None
        ):
            raise AssertionError(
                f"released lease remained usable: {response.status_code} {stale_body}"
            )
        print(f"execute_after_release=PASS code={stale_body.get('code')}")

        state["status"] = "released"
        Path(args.state_file).write_text(
            json.dumps(state, indent=2), encoding="utf-8"
        )
        print(f"state=PASS status=released file={args.state_file}")
        return 0
    except Exception as exc:
        print(f"FAIL: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        api.close()


if __name__ == "__main__":
    raise SystemExit(main())
