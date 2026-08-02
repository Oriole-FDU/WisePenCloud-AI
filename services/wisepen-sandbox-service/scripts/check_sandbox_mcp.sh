#!/usr/bin/env bash
set -euo pipefail

BASE_URL="http://127.0.0.1:19905"
SOURCE="APISIX-wX0iR6tY"
USER_ID="mcp-curl-user"
SESSION_ID="mcp-curl-session"
REQUEST_ID="mcp-curl-$(date +%s)-$RANDOM"
LEASE_ACQUIRED=0
RELEASE_COMPLETED=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-url) BASE_URL="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --user-id) USER_ID="$2"; shift 2 ;;
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --request-id) REQUEST_ID="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: check_sandbox_mcp.sh [--base-url URL] [--source VALUE] [--user-id VALUE] [--session-id VALUE] [--request-id VALUE]"
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

MCP_URL="${BASE_URL%/}/mcp/"
MCP_HEADERS=(
  -H "Accept: application/json, text/event-stream"
  -H "Content-Type: application/json"
  -H "X-From-Source: ${SOURCE}"
  -H "X-User-Id: ${USER_ID}"
  -H "X-Session-Id: ${SESSION_ID}"
  -H "X-Request-Id: ${REQUEST_ID}"
)

mcp_post() {
  local timeout_seconds="$1"
  local payload="$2"
  curl -fsS --max-time "$timeout_seconds" "${MCP_HEADERS[@]}" \
    -X POST "$MCP_URL" --data "$payload"
}

release_on_exit() {
  if [[ "$LEASE_ACQUIRED" != 1 || "$RELEASE_COMPLETED" == 1 ]]; then
    return
  fi
  if ! mcp_post 120 '{"jsonrpc":"2.0","id":999,"method":"tools/call","params":{"name":"release_sandbox","arguments":{}}}' >/dev/null; then
    echo "mcp_release_cleanup=FAILED request_id=${REQUEST_ID}" >&2
  fi
}

trap release_on_exit EXIT

parse_json() {
  python3 - "$1" <<'PY'
import json
import sys

raw = sys.argv[1].strip()
if raw.startswith("data:"):
    raw = "\n".join(line[5:].strip() for line in raw.splitlines() if line.startswith("data:"))
try:
    value = json.loads(raw)
except json.JSONDecodeError:
    candidates = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
    value = json.loads(candidates[-1]) if candidates else None
if value is None:
    raise SystemExit(f"MCP response is not JSON: {raw[:300]}")
print(json.dumps(value, ensure_ascii=False))
PY
}

INITIALIZE=$(mcp_post 30 '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"wisepen-curl","version":"1.0"}}}')
parse_json "$INITIALIZE" >/dev/null
echo "mcp_initialize=PASS"
mcp_post 30 '{"jsonrpc":"2.0","method":"notifications/initialized","params":{}}' >/dev/null || true

TOOLS_JSON=$(parse_json "$(mcp_post 30 '{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}')")
python3 - "$TOOLS_JSON" <<'PY'
import json
import sys

response = json.loads(sys.argv[1])
names = {item["name"] for item in response.get("result", {}).get("tools", [])}
expected = {
    "acquire_sandbox", "release_sandbox", "read_file", "write_file",
    "list_directory", "grep_files", "edit_file", "shell_exec",
    "run_sandbox_script",
}
missing = sorted(expected - names)
if missing:
    raise SystemExit(f"MCP tools missing: {missing}; discovered={sorted(names)}")
print("mcp_tools=PASS " + ",".join(sorted(names)))
PY

ACQUIRE_JSON=$(parse_json "$(mcp_post 60 '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"acquire_sandbox","arguments":{}}}')")
python3 - "$ACQUIRE_JSON" <<'PY'
import json
import sys

response = json.loads(sys.argv[1])
payload = response.get("result", {}).get("structuredContent", {})
if not payload.get("lease_id") or not payload.get("sandbox_id"):
    raise SystemExit(f"acquire_sandbox returned no lease: {response}")
print("mcp_acquire=PASS lease_id=" + payload["lease_id"])
PY
LEASE_ACQUIRED=1

RUN_JSON=$(parse_json "$(mcp_post 60 '{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"run_sandbox_script","arguments":{"language":"python","code":"print(\"mcp-curl-e2e\")"}}}')")
python3 - "$RUN_JSON" <<'PY'
import json
import sys

response = json.loads(sys.argv[1])
payload = response.get("result", {}).get("structuredContent", {})
if payload.get("status") not in {"succeeded", "completed"}:
    raise SystemExit(f"run_sandbox_script failed: {response}")
if "mcp-curl-e2e" not in json.dumps(payload, ensure_ascii=False):
    raise SystemExit(f"script marker missing: {response}")
print("mcp_run_sandbox_script=PASS")
PY

RELEASE_JSON=$(parse_json "$(mcp_post 120 '{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"release_sandbox","arguments":{}}}')")
python3 - "$RELEASE_JSON" <<'PY'
import json
import sys

response = json.loads(sys.argv[1])
payload = response.get("result", {}).get("structuredContent", {})
if payload.get("status") != "released":
    raise SystemExit(f"release_sandbox failed: {response}")
print("mcp_release=PASS")
PY
RELEASE_COMPLETED=1
