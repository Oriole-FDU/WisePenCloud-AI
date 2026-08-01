#!/usr/bin/env bash
set -euo pipefail

CHAT_URL="http://127.0.0.1:19904"
SOURCE="APISIX-wX0iR6tY"
USER_ID="chat-curl-user"
SESSION_ID="chat-curl-session"
REQUEST_ID="chat-curl-request"
MODEL_ID="${CHAT_MODEL_ID:-}"
PROVIDER_ID="${CHAT_PROVIDER_ID:-}"
DEVELOPER="${CHAT_DEVELOPER:-${DEVELOPER_NAME:-}}"
QUERY="请使用 run_sandbox_script 执行 Python 代码 print('chat-mcp-curl-e2e')，并返回执行结果。"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --chat-url) CHAT_URL="$2"; shift 2 ;;
    --source) SOURCE="$2"; shift 2 ;;
    --user-id) USER_ID="$2"; shift 2 ;;
    --session-id) SESSION_ID="$2"; shift 2 ;;
    --request-id) REQUEST_ID="$2"; shift 2 ;;
    --model-id) MODEL_ID="$2"; shift 2 ;;
    --provider-id) PROVIDER_ID="$2"; shift 2 ;;
    --developer) DEVELOPER="$2"; shift 2 ;;
    --query) QUERY="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: run_chat_request.sh [--chat-url URL] [--source VALUE] [--user-id VALUE] [--session-id VALUE] [--request-id VALUE] [--model-id ID] [--provider-id ID] [--developer NAME] [--query TEXT]"
      exit 0
      ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
  esac
done

if [[ -z "$MODEL_ID" ]]; then
  echo "--model-id or CHAT_MODEL_ID is required" >&2
  exit 2
fi

BODY=$(python3 - "$SESSION_ID" "$QUERY" "$MODEL_ID" "$PROVIDER_ID" <<'PY'
import json
import sys

body = {
    "session_id": sys.argv[1],
    "query": sys.argv[2],
    "model": sys.argv[3],
    "runtime_options": {},
}
if sys.argv[4]:
    body["provider_id"] = sys.argv[4]
print(json.dumps(body, ensure_ascii=False))
PY
)

HEADERS=(
  -H "Accept: text/event-stream"
  -H "Content-Type: application/json"
  -H "X-From-Source: ${SOURCE}"
  -H "X-User-Id: ${USER_ID}"
  -H "X-Session-Id: ${SESSION_ID}"
  -H "X-Request-Id: ${REQUEST_ID}"
)
if [[ -n "$DEVELOPER" ]]; then
  HEADERS+=(-H "X-Developer: ${DEVELOPER}")
fi

curl -fsS -N --max-time "${CHAT_TIMEOUT_SECONDS:-180}" \
  "${HEADERS[@]}" \
  -X POST "${CHAT_URL%/}/chat/completions" \
  --data "$BODY"
