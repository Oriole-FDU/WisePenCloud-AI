"""
WebSearchTool 端到端测试

通过 HTTP 调用 /chat/completions 接口，让 AI 自主决策调用 web_search 工具，
验证完整的 SSE 流式协议，并检查搜索结果质量。

测试用例覆盖：英文查询 / 中文查询 / 图片搜索 / 空结果 / 无效查询

使用方式:
    uv run python test/test_web_search_e2e.py
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any, Dict, List

import httpx

from test_common import (
    BASE_URL,
    analyze_events,
    check_health,
    count_tool_calls,
    create_session,
    delete_session,
    make_headers,
    shorten,
    stream_chat,
)

TIMEOUT_SECONDS = int(__import__("os").getenv("E2E_TIMEOUT", "120"))

# ─────────────────────────────────────────────────────────────
# 测试用例：(name, query, expected_success, with_images_hint)
# ─────────────────────────────────────────────────────────────
TEST_CASES = [
    ("英文-事实查询", "What is the capital of France?", True, False),
    ("英文-技术查询", "Python asyncio gather vs wait difference", True, False),
    ("中文-事实查询", "2024年诺贝尔物理学奖得主是谁", True, False),
    ("中文-技术查询", "Docker compose 网络配置最佳实践", True, False),
    ("图片搜索", "埃菲尔铁塔的图片", True, True),
    ("空结果-随机乱码", "xyznonexistentquery12345 qwertyuiop", False, False),
]


def build_query(name: str, query: str, expected_success: bool, with_images_hint: bool) -> str:
    if with_images_hint:
        return (
            f"请使用 web_search 工具搜索以下内容，并包含图片结果：\n"
            f"{query}\n\n"
            f"要求：必须调用 web_search 工具完成，with_images 设为 true。"
        )
    if expected_success:
        return (
            f"请使用 web_search 工具搜索以下内容，然后用中文简要总结搜索结果：\n"
            f"{query}\n\n"
            f"要求：必须调用 web_search 工具完成，不要只用文字描述。"
        )
    return (
        f"请使用 web_search 工具搜索以下内容：\n"
        f"{query}\n\n"
        f"要求：必须调用 web_search 工具，如果搜索无结果请说明。"
    )


async def run_single_test(
    client: httpx.AsyncClient,
    name: str,
    query: str,
    expected_success: bool,
    with_images_hint: bool,
    idx: int,
) -> Dict[str, Any]:
    prompt = build_query(name, query, expected_success, with_images_hint)

    print(f"\n{'='*80}")
    print(f"测试 #{idx}: {name}")
    print(f"查询: {query}")
    print(f"期望: {'成功' if expected_success else '无结果(按预期)'}")
    print(f"图片: {'是' if with_images_hint else '否'}")
    print(f"{'='*80}")

    session_id = await create_session(client, title=f"web_search E2E - {name}")
    print(f"会话已创建: {session_id}")

    try:
        t0 = time.monotonic()
        events = await stream_chat(client, session_id, prompt, timeout_seconds=TIMEOUT_SECONDS)
        elapsed = time.monotonic() - t0

        analysis = analyze_events(events)
        analysis["name"] = name
        analysis["query"] = query
        analysis["expected_success"] = expected_success
        analysis["with_images_hint"] = with_images_hint
        analysis["session_id"] = session_id
        analysis["elapsed_seconds"] = round(elapsed, 2)

        web_search_count = count_tool_calls(analysis, "web_search")
        analysis["web_search_called"] = web_search_count > 0
        analysis["web_search_call_count"] = web_search_count

        print(f"\n--- 事件统计 ---")
        print(f"总事件数: {analysis['total_events']}")
        print(f"耗时: {analysis['elapsed_seconds']}s")
        print(f"Agent Steps: {analysis['steps']}")
        print(f"协议完整性: start={analysis['has_start']} finish={analysis['has_finish']}")
        print(f"web_search 调用数: {web_search_count}")
        print(f"错误数: {len(analysis['errors'])}")

        if analysis["full_text"]:
            print(f"\n--- AI 回复预览 ---")
            print(shorten(analysis["full_text"], 500))

        if analysis["errors"]:
            print(f"\n--- 错误 ---")
            for err in analysis["errors"]:
                print(f"  ✗ {err}")

        return analysis

    except Exception as e:
        print(f"\n✗ 测试 #{idx} 异常: {e}")
        return {
            "name": name,
            "query": query,
            "expected_success": expected_success,
            "with_images_hint": with_images_hint,
            "error": str(e),
        }
    finally:
        await delete_session(client, session_id)


async def main() -> int:
    print("WebSearchTool 端到端测试")
    print(f"目标: {BASE_URL}")
    print(f"用户: {__import__('os').getenv('TEST_USER_ID', 'e2e-test-user')}")
    print(f"用例数: {len(TEST_CASES)}")

    async with httpx.AsyncClient(base_url=BASE_URL, headers=make_headers()) as client:
        if not await check_health(client):
            print(f"\n✗ 服务不可达: {BASE_URL}")
            print("请先启动 chat-service: uv run python -m chat.main")
            return 1

        print("✓ 服务可达")

        results: List[Dict[str, Any]] = []
        for idx, (name, query, expected, with_images) in enumerate(TEST_CASES, 1):
            result = await run_single_test(client, name, query, expected, with_images, idx)
            results.append(result)

    print(f"\n\n{'='*80}")
    print("汇总报告")
    print(f"{'='*80}")

    passed = 0
    failed = 0

    for r in results:
        name = r.get("name", "?")

        if "error" in r:
            print(f"  ✗ FAIL {name}: 异常 - {r['error']}")
            failed += 1
            continue

        web_search_called = r.get("web_search_called", False)
        has_start = r.get("has_start", False)
        has_finish = r.get("has_finish", False)
        steps = r.get("steps", 0)
        expected = r.get("expected_success", True)

        ok = web_search_called and has_start and has_finish and steps > 0

        if ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"

        reasons = []
        if not web_search_called:
            reasons.append("web_search 未被调用")
        if not has_start:
            reasons.append("缺少 start")
        if not has_finish:
            reasons.append("缺少 finish")
        if steps == 0:
            reasons.append("无 Agent Step")

        expected_tag = "" if expected else " [期望无结果]"
        detail = f" ({', '.join(reasons)})" if reasons else ""
        print(
            f"  {status} {name}{expected_tag}: "
            f"steps={steps}, web_search={r.get('web_search_call_count', 0)}, "
            f"elapsed={r.get('elapsed_seconds', '?')}s{detail}"
        )

    print(f"\n通过: {passed}/{passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
