#!/usr/bin/env python3
import asyncio
import json
import sys
from pathlib import Path
from httpx import AsyncClient

src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

common_dir = Path(__file__).parent.parent.parent / "wisepen-common" / "src"
sys.path.insert(0, str(common_dir))


API_BASE_URL = "http://localhost:8000"
FROM_SOURCE_SECRET = "APISIX-wX0iR6tY"
USER_ID = "123"
TIMEOUT = 120.0
PAPER_URL = "https://arxiv.org/abs/2310.06825"

COMMON_HEADERS = {
    "Content-Type": "application/json",
    "X-From-Source": FROM_SOURCE_SECRET,
    "X-User-Id": USER_ID,
}


async def main():
    async with AsyncClient(timeout=TIMEOUT) as client:
        # Step 1: 创建会话
        print("\n" + "=" * 60)
        print("Step 1: Creating session...")
        print("=" * 60)

        create_response = await client.post(
            f"{API_BASE_URL}/chat/session/createSession",
            headers=COMMON_HEADERS,
            json={"title": "Test Browse URL"},
        )

        print(f"Status: {create_response.status_code}")
        if create_response.is_success:
            result = create_response.json()
            print(f"Response: {json.dumps(result, ensure_ascii=False, indent=2)}")
            session_id = result["data"]["id"]
            print(f"\nSession ID: {session_id}")
        else:
            print(f"Error: {create_response.text}")
            return

        # Step 2: 发送请求让 AI 使用 browse_url 工具
        print("\n" + "=" * 60)
        print("Step 2: Sending chat message...")
        print("=" * 60)

        chat_data = {
            "session_id": session_id,
            "query": f"请帮我抓取这篇论文的内容：{PAPER_URL}",
            "model": 1,
        }

        print(f"Request: {json.dumps(chat_data, ensure_ascii=False, indent=2)}")
        print("\nStreaming response:")
        print("-" * 60)

        full_response_text = ""
        all_events = []

        async with client.stream(
            "POST",
            f"{API_BASE_URL}/chat/completions",
            headers=COMMON_HEADERS,
            json=chat_data,
        ) as response:
            print(f"Response status: {response.status_code}")

            if not response.is_success:
                error_text = await response.aread()
                print(f"Error: {error_text.decode('utf-8')}")
                return

            try:
                async for line in response.aiter_lines():
                    if line.strip().startswith("data:"):
                        data_str = line.strip()[5:].strip()
                        if not data_str:
                            continue
                        if data_str == "[DONE]":
                            print("\n[Stream done]")
                            break

                        try:
                            data = json.loads(data_str)
                            all_events.append(data)
                            event_type = data.get("type", "")

                            if event_type == "tool-input-start":
                                print(f"\n🔧 [TOOL CALL] {data.get('toolName')} (id={data.get('toolCallId')})")
                            elif event_type == "tool-input-available":
                                print(f"   [TOOL INPUT] {json.dumps(data.get('input', {}), ensure_ascii=False)}")
                            elif event_type == "tool-output-available":
                                output = data.get("output", "")
                                preview = str(output)[:300]
                                print(f"   [TOOL OUTPUT] {preview}{'...' if len(str(output)) > 300 else ''}")
                            elif event_type == "text-delta" and data.get("delta"):
                                full_response_text += data["delta"]
                                print(data["delta"], end="", flush=True)
                            elif event_type in ("start", "finish", "text-start", "text-end",
                                                "start-step", "finish-step", "reasoning-start",
                                                "reasoning-delta", "reasoning-end"):
                                pass
                            else:
                                print(f"\n[EVENT] {event_type}: {json.dumps(data, ensure_ascii=False)[:200]}")
                        except json.JSONDecodeError:
                            print(f"\n[JSON parse error]: {data_str}")
            except Exception as e:
                print(f"\n[Stream error]: {type(e).__name__}: {e}")

        print("-" * 60)
        print(f"\n\nFull response ({len(full_response_text)} chars):")
        print(full_response_text[:2000])
        if len(full_response_text) > 2000:
            print(f"\n... (truncated, total {len(full_response_text)} chars)")

        print(f"\nTotal events: {len(all_events)}")
        print(f"Event types: {[e.get('type') for e in all_events]}")
        print("\n" + "=" * 60)
        print("Test completed!")
        print("=" * 60)


if __name__ == "__main__":
    asyncio.run(main())
