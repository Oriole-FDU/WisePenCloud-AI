"""
WebFetchTool 端到端测试

通过 HTTP 调用 /chat/completions 接口，让 AI 自主决策调用 web_fetch 工具，
验证完整的 SSE 流式协议，并检查抓取结果质量。

测试用例覆盖：PDF / DOCX / XLSX / PPTX / XML / JSON / CSV / HTML / 中文页面 / 负向

使用方式:
    uv run python test/test_web_fetch_e2e.py
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
    has_tool_call,
    make_headers,
    shorten,
    stream_chat,
)

TIMEOUT_SECONDS = int(__import__("os").getenv("E2E_TIMEOUT", "120"))

# ─────────────────────────────────────────────────────────────
# 测试用例：(name, url, expected_success)
# ─────────────────────────────────────────────────────────────
TEST_CASES = [
    # PDF
    ("PDF-Attention Is All You Need", "https://arxiv.org/pdf/1706.03762", True),
    # DOCX
    ("DOCX-calibre 官方示例", "https://calibre-ebook.com/downloads/demos/demo.docx", True),

    # XLSX
    ("XLSX-Frictionless 单 Sheet", "https://raw.githubusercontent.com/frictionlessdata/datasets/main/files/excel/sample-1-sheet.xlsx", True),

    # PPTX
    ("PPTX-Microsoft Workshop Template", "https://raw.githubusercontent.com/microsoft/workshop-template/main/presentation.pptx", True),

    # XML
    ("XML-Python 官方文档 Sitemap", "https://docs.python.org/sitemap.xml", True),

    # JSON
    ("JSON-GitHub CPython Repo API", "https://api.github.com/repos/python/cpython", True),

    # CSV
    ("CSV-Seaborn Titanic", "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/titanic.csv", True),

    # HTML
    ("HTML-Example.com", "https://example.com/", True),
    ("HTML-Python 文档首页", "https://docs.python.org/3/", True),

    # 中文页面
    ("中文-中科大镜像", "https://mirrors.ustc.edu.cn/", True),

    # 负向：应该失败
    ("HTTP-404", "https://httpbin.org/status/404", False),
    ("HTTP-空响应", "https://httpbin.org/status/204", False),
    ("HTTP-不支持二进制", "https://httpbin.org/bytes/1024", False),
]


def build_query(name: str, url: str, expected_success: bool) -> str:
    if expected_success:
        return (
            f"请使用 web_fetch 工具抓取以下 URL 的内容，然后用中文简要总结你获取到的内容要点：\n"
            f"{url}\n\n"
            f"要求：必须调用 web_fetch 工具完成，不要只用文字描述。"
        )
    return (
        f"请使用 web_fetch 工具尝试抓取以下 URL：\n"
        f"{url}\n\n"
        f"要求：必须调用 web_fetch 工具，如果抓取失败请说明失败原因。"
    )


async def run_single_test(
    client: httpx.AsyncClient,
    name: str,
    url: str,
    expected_success: bool,
    idx: int,
) -> Dict[str, Any]:
    query = build_query(name, url, expected_success)

    print(f"\n{'='*80}")
    print(f"测试 #{idx}: {name}")
    print(f"URL:  {url}")
    print(f"期望: {'成功' if expected_success else '失败(按预期)'}")
    print(f"{'='*80}")

    session_id = await create_session(client, title=f"web_fetch E2E - {name}")
    print(f"会话已创建: {session_id}")

    try:
        t0 = time.monotonic()
        events = await stream_chat(client, session_id, query, timeout_seconds=TIMEOUT_SECONDS)
        elapsed = time.monotonic() - t0

        analysis = analyze_events(events)
        analysis["name"] = name
        analysis["url"] = url
        analysis["expected_success"] = expected_success
        analysis["session_id"] = session_id
        analysis["elapsed_seconds"] = round(elapsed, 2)

        web_fetch_count = count_tool_calls(analysis, "web_fetch")
        analysis["web_fetch_called"] = web_fetch_count > 0
        analysis["web_fetch_call_count"] = web_fetch_count

        print(f"\n--- 事件统计 ---")
        print(f"总事件数: {analysis['total_events']}")
        print(f"耗时: {analysis['elapsed_seconds']}s")
        print(f"Agent Steps: {analysis['steps']}")
        print(f"协议完整性: start={analysis['has_start']} finish={analysis['has_finish']}")
        print(f"web_fetch 调用数: {web_fetch_count}")
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
            "url": url,
            "expected_success": expected_success,
            "error": str(e),
        }
    finally:
        await delete_session(client, session_id)


async def main() -> int:
    print("WebFetchTool 端到端测试")
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
        for idx, (name, url, expected) in enumerate(TEST_CASES, 1):
            result = await run_single_test(client, name, url, expected, idx)
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

        web_fetch_called = r.get("web_fetch_called", False)
        has_start = r.get("has_start", False)
        has_finish = r.get("has_finish", False)
        steps = r.get("steps", 0)
        expected = r.get("expected_success", True)

        ok = web_fetch_called and has_start and has_finish and steps > 0

        if ok:
            passed += 1
            status = "✓ PASS"
        else:
            failed += 1
            status = "✗ FAIL"

        reasons = []
        if not web_fetch_called:
            reasons.append("web_fetch 未被调用")
        if not has_start:
            reasons.append("缺少 start")
        if not has_finish:
            reasons.append("缺少 finish")
        if steps == 0:
            reasons.append("无 Agent Step")

        expected_tag = "" if expected else " [期望失败]"
        detail = f" ({', '.join(reasons)})" if reasons else ""
        print(
            f"  {status} {name}{expected_tag}: "
            f"steps={steps}, web_fetch={r.get('web_fetch_call_count', 0)}, "
            f"elapsed={r.get('elapsed_seconds', '?')}s{detail}"
        )

    print(f"\n通过: {passed}/{passed + failed}")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
