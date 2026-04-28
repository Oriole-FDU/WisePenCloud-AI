#!/usr/bin/env python3
import asyncio
import json
from httpx import AsyncClient

# Configuration
API_BASE_URL = 'http://localhost:8000'
FROM_SOURCE_SECRET = 'APISIX-wX0iR6tY'
USER_ID = '123'
TIMEOUT = 60.0  # 增加超时时间


def add_log(message, type='info'):
    """简单的日志函数"""
    print(f"[{type.upper()}] {message}")


async def main():
    async with AsyncClient(timeout=TIMEOUT) as client:
        # Step 1: Create a session
        print("\n" + "="*60)
        print("Step 1: Creating session...")
        print("="*60)
        
        create_response = await client.post(
            f"{API_BASE_URL}/chat/session/createSession",
            headers={
                'Content-Type': 'application/json',
                'X-From-Source': FROM_SOURCE_SECRET,
                'X-User-Id': USER_ID
            },
            json={'title': 'Test Chat'}
        )
        
        print(f"Status: {create_response.status_code}")
        if create_response.is_success:
            result = create_response.json()
            print(f"Response: {json.dumps(result, ensure_ascii=False, indent=2)}")
            session_id = result['data']['id']
            print(f"\nSession ID created: {session_id}")
        else:
            print(f"Error: {create_response.text}")
            return
        
        # Step 2: Send a chat message
        print("\n" + "="*60)
        print("Step 2: Sending chat message...")
        print("="*60)
        
        chat_data = {
            'session_id': session_id,
            'query': input("请输入您的问题: "),
            'model': 1
        }
        
        print(f"Request data: {json.dumps(chat_data, ensure_ascii=False, indent=2)}")
        print("\nStreaming response:")
        print("-" * 60)
        
        full_response_text = ""
        all_events = []  # 保存所有事件
        
        async with client.stream(
            'POST',
            f"{API_BASE_URL}/chat/completions",
            headers={
                'Content-Type': 'application/json',
                'X-From-Source': FROM_SOURCE_SECRET,
                'X-User-Id': USER_ID
            },
            json=chat_data
        ) as response:
            print(f"Response status: {response.status_code}")
            print(f"Response headers: {dict(response.headers)}")
            
            if not response.is_success:
                error_text = await response.aread()
                print(f"Error: {error_text.decode('utf-8')}")
                return
            
            # Process streaming response
            try:
                async for line in response.aiter_lines():
                    if line.strip().startswith('data:'):
                        data_str = line.strip()[5:].strip()
                        if data_str:
                            # 处理 [DONE] 标记
                            if data_str == "[DONE]":
                                add_log("接收到 [DONE] 标记，流式响应结束", "info")
                                break
                            
                            try:
                                data = json.loads(data_str)
                                all_events.append(data)
                                add_log(f"Event: {json.dumps(data, ensure_ascii=False)}", "info")
                                
                                if data.get('type') == 'text-delta' and data.get('delta'):
                                    full_response_text += data['delta']
                                    print(f"当前响应: {full_response_text}")
                            except json.JSONDecodeError as e:
                                add_log(f"JSON 解析失败: {e}, data: {data_str}", "error")
            except Exception as e:
                add_log(f"读取流时出错: {type(e).__name__}: {e}", "error")
                import traceback
                print(f"堆栈:\n{traceback.format_exc()}")
        
        print("-" * 60)
        print(f"\nFull response text: {full_response_text}")
        print(f"\nTotal events: {len(all_events)}")
        print(f"Event types: {[e.get('type') for e in all_events]}")
        print("\n" + "="*60)
        print("Test completed!")
        print("="*60)


if __name__ == "__main__":
    asyncio.run(main())
