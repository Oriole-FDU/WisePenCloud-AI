"""
BrowseInteractTool 端到端测试

通过 HTTP 调用 /chat/completions 接口，让 AI 自主决策调用 browse_interact 工具，
验证完整的 SSE 流式协议，并实时打印每一步输出。

复杂用例：
1. 打开维基百科英语首页，获取页面快照并总结内容。
2. 打开 GitHub 页面，输入邮箱和密码并登录。

使用方式:
    uv run python test/test_browse_interact_e2e.py
"""
from __future__ import annotations

import asyncio
import os
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

TIMEOUT_SECONDS = int(os.getenv("E2E_TIMEOUT", "300"))

# ─────────────────────────────────────────────────────────────
# 测试 query：复杂浏览交互用例
# ─────────────────────────────────────────────────────────────
TEST_QUERIES = [
    {
        "name": "浏览维基百科首页",
        "query": "请用 browse_interact 浏览器工具打开维基百科英语首页 https://en.wikipedia.org/wiki/Main_Page ，搜索 apple，告诉我内容",
    },
    {
        "name": "浏览Bilibili首页",
        "query": "请用 browse_interact 浏览器工具打开 Bilibili首页 https://www.bilibili.com/ ，搜索 复旦大学， 点击一个视频， 10 秒后关闭视频，并回到首页，告诉我首页有哪些视频",
    },
]


def print_summary(analysis: Dict[str, Any]) -> None:
    print("\n--- 事件统计 ---")
    print(f"总事件数: {analysis['total_events']}")
    print(f"耗时: {analysis['elapsed_seconds']}s")
    print(f"Agent Steps: {analysis['steps']}")
    print(
        "协议完整性: "
        f"start={analysis['has_start']} "
        f"finish={analysis['has_finish']} "
        f"done={analysis['has_done']}"
    )
    print(f"browse_interact 调用数: {analysis['browse_interact_call_count']}")
    print(f"并行调用次数: {analysis.get('parallel_tool_calls_count', 0)}")
    print(f"工具失败数: {analysis.get('tool_failure_count', 0)}")
    print(f"可恢复失败数: {analysis.get('recovered_tool_failure_count', 0)}")
    print(f"致命失败数: {analysis.get('fatal_tool_failure_count', 0)}")
    print(f"get_content 调用: {analysis.get('get_content_called', False)}")
    print(f"事件错误数: {len(analysis['errors'])}")

    if analysis["tool_calls"]:
        print("\n--- 工具调用摘要 ---")
        for tc in analysis["tool_calls"]:
            phase = tc.get("phase")
            if phase == "input_start":
                print(f"  ▶ {tc.get('tool_name')} call_id={tc.get('call_id')}")
            elif phase == "input_available":
                print(f"    input: {shorten(tc.get('input', {}), 300)}")
            elif phase == "output_available":
                output = tc.get("output", "")
                success = tc.get("success", True)
                success_mark = "✅" if success else "❌"
                if "snapshot" in output and "tree" in output:
                    print(f"    {success_mark} output: [snapshot - {len(output)} chars, shown in terminal above]")
                else:
                    print(f"    {success_mark} output: {tc.get('output_preview', '')}")

    if analysis["full_text"]:
        print("\n--- AI 最终回答 ---")
        print(analysis["full_text"])

    if analysis["errors"]:
        print("\n--- 错误 ---")
        for err in analysis["errors"]:
            print(f"  ✗ {err}")


async def run_single_test(
    client: httpx.AsyncClient,
    test_case: Dict[str, Any],
    idx: int,
) -> Dict[str, Any]:
    name = test_case["name"]
    query = test_case["query"]

    print(f"\n{'=' * 80}")
    print(f"测试 #{idx}: {name}")
    print(f"Query:\n{query}")
    print(f"{'=' * 80}")

    session_id = await create_session(client, title=f"browse_interact E2E - {name}")
    print(f"会话已创建: {session_id}")

    try:
        t0 = time.monotonic()
        events = await stream_chat(client, session_id, query, timeout_seconds=TIMEOUT_SECONDS)
        elapsed = time.monotonic() - t0

        analysis = analyze_events(events)
        analysis["name"] = name
        analysis["session_id"] = session_id
        analysis["elapsed_seconds"] = round(elapsed, 2)

        browse_count = count_tool_calls(analysis, "browse_interact")
        analysis["browse_interact_called"] = browse_count > 0
        analysis["browse_interact_call_count"] = browse_count

        print_summary(analysis)
        return analysis

    finally:
        await delete_session(client, session_id)


async def main() -> int:
    print("BrowseInteractTool 复杂端到端测试")
    print(f"目标: {BASE_URL}")
    print(f"超时: {TIMEOUT_SECONDS}s")

    async with httpx.AsyncClient(base_url=BASE_URL, headers=make_headers()) as client:
        if not await check_health(client):
            print(f"\n✗ 服务不可达: {BASE_URL}")
            print("请先启动 chat-service: uv run python -m chat.main")
            return 1

        print("✓ 服务可达")

        results: List[Dict[str, Any]] = []
        for idx, tc in enumerate(TEST_QUERIES, 1):
            try:
                result = await run_single_test(client, tc, idx)
                results.append(result)
            except Exception as e:
                print(f"\n✗ 测试 #{idx} 异常: {e}")
                results.append({"error": str(e), "name": tc["name"]})

    print(f"\n\n{'=' * 80}")
    print("汇总报告")
    print(f"{'=' * 80}")

    passed = 0
    failed = 0

    for idx, result in enumerate(results, 1):
        name = result.get("name", f"测试#{idx}")

        if "error" in result:
            print(f"  ✗ FAIL {name}: 异常 - {result['error']}")
            failed += 1
            continue

        has_browse = result.get("browse_interact_called", False)
        browse_count = result.get("browse_interact_call_count", 0)
        has_start = result.get("has_start", False)
        has_finish = result.get("has_finish", False)
        steps = result.get("steps", 0)
        errors = result.get("errors", [])
        tool_failure_count = result.get("tool_failure_count", 0)
        fatal_tool_failure_count = result.get("fatal_tool_failure_count", 0)
        parallel_calls = result.get("parallel_tool_calls_count", 0)
        get_content_called = result.get("get_content_called", False)

        ok = (
            has_start
            and has_finish
            and steps > 0
            and len(errors) == 0
            and fatal_tool_failure_count == 0
        )

        if ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"

        reasons = []
        if not has_start:
            reasons.append("缺少 start 事件")
        if not has_finish:
            reasons.append("缺少 finish 事件")
        if steps == 0:
            reasons.append("无 Agent Step")
        if errors:
            reasons.append(f"存在错误({len(errors)})")
        if fatal_tool_failure_count > 0:
            reasons.append(f"有致命失败({fatal_tool_failure_count})")

        detail = f" ({', '.join(reasons)})" if reasons else ""
        print(
            f"  {status} {name}: "
            f"steps={steps}, browse_interact={browse_count}, "
            f"tool_failures={tool_failure_count}, "
            f"parallel_calls={parallel_calls}, "
            f"get_content={get_content_called}, "
            f"elapsed={result.get('elapsed_seconds', '?')}s{detail}"
        )

    print(f"\n通过: {passed}/{passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
