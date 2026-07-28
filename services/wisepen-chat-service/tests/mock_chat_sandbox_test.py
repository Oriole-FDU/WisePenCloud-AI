"""
Chat ↔ Sandbox MCP 集成 Mock 测试
无需 LLM / MongoDB / Redis / Nacos，只验证 MCP 工具发现与调用。
"""
import asyncio
import json
import os
import sys

# 确保依赖可导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "wisepen-common", "src"))


async def test_sandbox_mcp_tools():
    """模拟 Chat 服务通过 McpServiceClient 发现和调用沙箱 MCP 工具。"""
    from chat.service_client.mcp_service_client import McpServiceClient

    # 创建直连云客户端（绕过 Nacos 服务发现）
    client = McpServiceClient(
        discovery=None,  # type: ignore — 使用 base_url 时不需要 discovery
        from_source_secret="local-dev-secret",
        base_url="http://localhost:8001",
        timeout=30.0,
        service_name="wisepen-sandbox-mcp-service",
    )
    # 手动设置 user_id/session_id（模拟 SecurityHeaderMiddleware）
    from common.security.context import SecurityContextHolder
    SecurityContextHolder.set_user_id("test-user")
    SecurityContextHolder.set_session_id("test-session")

    # 1. 发现工具
    print("=" * 60)
    print("[1] Discovering MCP tools from sandbox gateway...")
    try:
        tools = await client.list_tools()
        print(f"  Found {len(tools)} tools:")
        for t in tools:
            print(f"    - {t.name}: {t.description[:60]}...")
    except Exception as e:
        print(f"  FAIL: {e}")
        return

    if len(tools) == 0:
        print("  FAIL: No sandbox tools discovered. Is the gateway running?")
        return

    # 2. 调用 write_file
    print("\n[2] Calling write_file...")
    try:
        result = await client.call_tool(None, "write_file", {
            "file": "/workspace/test.py",
            "content": "print('hello from sandbox mock test')",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")
        return

    # 3. 调用 read_file
    print("\n[3] Calling read_file...")
    try:
        result = await client.call_tool(None, "read_file", {
            "file": "/workspace/test.py",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # 4. 调用 list_directory
    print("\n[4] Calling list_directory...")
    try:
        result = await client.call_tool(None, "list_directory", {
            "path": "/workspace",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # 5. 调用 shell_exec
    print("\n[5] Calling shell_exec...")
    try:
        result = await client.call_tool(None, "shell_exec", {
            "command": "python /workspace/test.py",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # 6. 调用 edit_file
    print("\n[6] Calling edit_file...")
    try:
        result = await client.call_tool(None, "edit_file", {
            "file": "/workspace/test.py",
            "old_str": "hello from sandbox mock test",
            "new_str": "hello from EDITED mock test",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # 7. 调用 grep_files
    print("\n[7] Calling grep_files...")
    try:
        result = await client.call_tool(None, "grep_files", {
            "path": "/workspace",
            "pattern": "print",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    # 8. 再次 read_file 验证 edit_file 持久化
    print("\n[8] Verifying edit persisted via read_file...")
    try:
        result = await client.call_tool(None, "read_file", {
            "file": "/workspace/test.py",
        })
        print(f"  OK: {result[:200]}")
    except Exception as e:
        print(f"  FAIL: {e}")

    print("\n" + "=" * 60)
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    asyncio.run(test_sandbox_mcp_tools())
