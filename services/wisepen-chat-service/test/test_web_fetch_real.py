#!/usr/bin/env python3
"""
真实测试脚本：覆盖多种内容类型的降级链路测试

测试维度：
  - PDF / DOCX / XLSX / PPTX 文档直链
  - XML / RSS / Sitemap
  - JSON / JSON-LD / API
  - CSV / TXT / Markdown
  - 静态 HTML 页面
  - 国内中文页面
  - SPA / JS 渲染页面
  - 强反爬 / 登录墙
  - HTTP 边界（状态码、重定向、空响应）
  - 真实 404 / 重定向
"""
import asyncio
import sys
from pathlib import Path

SERVICE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(SERVICE_ROOT / "src"))
sys.path.insert(0, str(SERVICE_ROOT.parent / "wisepen-common" / "src"))

from chat.application.web_fetch.fetch_coordinator import FetchCoordinator, FetchCoordinatorConfig

TEST_WEBSITES = [
    # ─────────────────────────────────────────────────────────────
    # PDF 文档直链：论文 / 手册 / 表格 / 小型测试
    # ─────────────────────────────────────────────────────────────
    ("PDF-Attention Is All You Need", "https://arxiv.org/pdf/1706.03762", True),
    ("PDF-BERT 预训练", "https://arxiv.org/pdf/1810.04805", True),
    ("PDF-GPT-4 Technical Report", "https://arxiv.org/pdf/2303.08774", True),
    ("PDF-IRS W4 表格", "https://www.irs.gov/pub/irs-pdf/fw4.pdf", True),
    ("PDF-calibre 英文手册", "https://manual.calibre-ebook.com/calibre.pdf", True),
    ("PDF-calibre 中文手册", "https://manual.calibre-ebook.com/zh_CN/calibre.pdf", True),

    # ─────────────────────────────────────────────────────────────
    # DOCX 文档直链：真实 DOCX，测试 python-docx 解析
    # ─────────────────────────────────────────────────────────────
    ("DOCX-calibre 官方示例", "https://calibre-ebook.com/downloads/demos/demo.docx", True),
    ("DOCX-GitHub Raw 小型样例", "https://raw.githubusercontent.com/rounakdatta/CorrectLy/master/sample.docx", True),

    # ─────────────────────────────────────────────────────────────
    # XLSX 文档直链：真实 Excel，测试 openpyxl read_only/data_only
    # ─────────────────────────────────────────────────────────────
    ("XLSX-Frictionless 单 Sheet 样例", "https://raw.githubusercontent.com/frictionlessdata/datasets/main/files/excel/sample-1-sheet.xlsx", True),
    ("XLSX-Gapminder 数据", "https://raw.githubusercontent.com/jennybc/gapminder/master/data-raw/xls/gapdata001-1.xlsx", True),
    ("XLSX-Plotly Excel 示例", "https://raw.githubusercontent.com/plotly/datasets/master/data-matlab-excel-example.xlsx", True),

    # ─────────────────────────────────────────────────────────────
    # PPTX 文档直链：真实 PowerPoint，测试 python-pptx
    # ─────────────────────────────────────────────────────────────
    ("PPTX-IETF Hackathon 模板", "https://raw.githubusercontent.com/IETF-Hackathon/ietf121-project-presentations/main/presentation-template.pptx", True),
    ("PPTX-AWS NLP Workshop", "https://raw.githubusercontent.com/aws-samples/aws-nlp-workshop/master/Presentation-AWS-NLP-workshop.pptx", True),
    ("PPTX-Microsoft Workshop Template", "https://raw.githubusercontent.com/microsoft/workshop-template/main/presentation.pptx", True),

    # ─────────────────────────────────────────────────────────────
    # XML / RSS / Sitemap：测试 application/xml、text/xml、+xml
    # ─────────────────────────────────────────────────────────────
    ("XML-W3C 新闻 RSS", "https://www.w3.org/blog/news/feed", True),
    ("XML-Python Package Index Sitemap", "https://pypi.org/sitemap.xml", True),
    ("XML-博客园 RSS", "https://www.cnblogs.com/rss", True),
    ("XML-36氪 Sitemap", "https://36kr.com/sitemap.xml", True),
    ("XML-Python 官方文档 Sitemap", "https://docs.python.org/sitemap.xml", True),

    # ─────────────────────────────────────────────────────────────
    # JSON / JSON-LD / API：测试 application/json、application/*+json
    # ─────────────────────────────────────────────────────────────
    ("JSON-GitHub CPython Repo API", "https://api.github.com/repos/python/cpython", True),
    ("JSON-GitHub Rust Repo API", "https://api.github.com/repos/rust-lang/rust", True),
    ("JSON-CNode 主题 API", "https://cnodejs.org/api/v1/topics", True),
    ("JSON-Schema.org JSON-LD Context", "https://schema.org/docs/jsonldcontext.jsonld", True),
    ("JSON-httpbin JSON", "https://httpbin.org/json", True),

    # ─────────────────────────────────────────────────────────────
    # CSV / TXT / Markdown：测试 text/*、扩展名兜底
    # ─────────────────────────────────────────────────────────────
    ("CSV-Plotly Gapminder", "https://raw.githubusercontent.com/plotly/datasets/master/gapminderDataFiveYear.csv", True),
    ("CSV-Seaborn Titanic", "https://raw.githubusercontent.com/mwaskom/seaborn-data/master/titanic.csv", True),
    ("TXT-GNU GPL 3.0 许可证", "https://www.gnu.org/licenses/gpl-3.0.txt", True),
    ("MD-CPython README", "https://raw.githubusercontent.com/python/cpython/main/README.rst", True),
    ("MD-Rust README", "https://raw.githubusercontent.com/rust-lang/rust/master/README.md", True),

    # ─────────────────────────────────────────────────────────────
    # 静态 HTML：测试 StaticFetcher + readability/markdownify
    # ─────────────────────────────────────────────────────────────
    ("HTML-Example.com", "https://example.com/", True),
    ("HTML-Python 文档首页", "https://docs.python.org/3/", True),
    ("HTML-HTTPX 文档", "https://www.python-httpx.org/", True),
    ("HTML-MDN Web Docs", "https://developer.mozilla.org/en-US/", True),
    ("HTML-W3C Standards", "https://www.w3.org/standards/", True),
    ("HTML-RFC Editor", "https://www.rfc-editor.org/", True),
    ("HTML-NIST CSRC", "https://csrc.nist.gov/", True),

    # ─────────────────────────────────────────────────────────────
    # 国内静态 / 半动态页面：测试中文编码、新闻站、技术站
    # ─────────────────────────────────────────────────────────────
    ("中文-博客园", "https://www.cnblogs.com/", True),
    ("中文-菜鸟教程", "https://www.runoob.com/", True),
    ("中文-V2EX", "https://www.v2ex.com/", True),
    ("中文-InfoQ 中国", "https://www.infoq.cn/", True),
    ("中文-中科大镜像", "https://mirrors.ustc.edu.cn/", True),
    ("中文-人民网", "https://www.people.com.cn/", True),
    ("中文-新华网", "https://www.xinhuanet.com/", True),
    ("中文-央视网", "https://www.cctv.com/", True),
    ("中文-中国政府网", "https://www.gov.cn/", True),

    # ─────────────────────────────────────────────────────────────
    # SPA / JS 渲染：应触发 Steel / LocalScript 降级链路
    # ─────────────────────────────────────────────────────────────
    ("SPA-B 站", "https://www.bilibili.com/", True),
    ("SPA-知乎", "https://www.zhihu.com/", True),
    ("SPA-小红书", "https://www.xiaohongshu.com/", True),
    ("SPA-豆瓣", "https://www.douban.com/", True),
    ("SPA-飞书", "https://www.feishu.cn/", True),
    ("SPA-掘金", "https://juejin.cn/", True),

    # ─────────────────────────────────────────────────────────────
    # 强反爬 / 登录墙 / 风控：测试降级和失败路径
    # ─────────────────────────────────────────────────────────────
    ("反爬-淘宝", "https://www.taobao.com/", False),
    ("反爬-京东", "https://www.jd.com/", False),
    ("反爬-抖音", "https://www.douyin.com/", False),
    ("反爬-微博", "https://weibo.com/", False),
    ("反爬-12306", "https://www.12306.cn/", False),

    # ─────────────────────────────────────────────────────────────
    # HTTP 边界：状态码、重定向、不支持类型、空内容
    # ─────────────────────────────────────────────────────────────
    ("HTTP-404", "https://httpbin.org/status/404", False),
    ("HTTP-500", "https://httpbin.org/status/500", False),
    ("HTTP-正常重定向", "https://httpbin.org/redirect/3", True),
    ("HTTP-过多重定向", "https://httpbin.org/redirect/50", False),
    ("HTTP-空响应", "https://httpbin.org/status/204", False),
    ("HTTP-不支持二进制", "https://httpbin.org/bytes/1024", False),

    # ─────────────────────────────────────────────────────────────
    # URL / 页面边界：真实 404、重定向、缺失页面
    # ─────────────────────────────────────────────────────────────
    ("真实404-GitHub", "https://github.com/this-page-should-not-exist-abcdefg", False),
    ("真实404-百度", "https://www.baidu.com/this-page-does-not-exist-404", False),
    ("真实404-政府网", "https://www.gov.cn/404.html", False),
    ("重定向-GitHub", "https://github.com/python", True),
]


async def test_website(coordinator: FetchCoordinator, name: str, url: str, expected_success: bool):
    """测试单个 URL"""
    print(f"\n{'='*80}")
    print(f"测试: {name}")
    print(f"URL:  {url}")
    print(f"期望: {'成功' if expected_success else '失败(按预期)'}")
    print(f"{'='*80}")

    try:
        result = await coordinator.fetch(url)
        actual_success = result is not None

        if actual_success == expected_success:
            status = "✅ 按预期成功" if actual_success else "✅ 按预期失败"
        else:
            status = "❌ 意外成功" if actual_success else "❌ 意外失败"

        print(f"{status}")
        if actual_success:
            print(f"   长度: {len(result)} 字符")
            print(f"   前200字符: {repr(result[:200])}")
        return {
            "name": name,
            "url": url,
            "expected_success": expected_success,
            "actual_success": actual_success,
            "passed": actual_success == expected_success,
            "length": len(result) if result else 0,
            "preview": result[:200] if result else "",
        }
    except Exception as e:
        actual_success = False
        passed = (not expected_success)
        print(f"{'✅ 按预期异常' if passed else '❌ 意外异常'}: {e}")
        return {
            "name": name,
            "url": url,
            "expected_success": expected_success,
            "actual_success": actual_success,
            "passed": passed,
            "length": 0,
            "preview": "",
            "error": str(e),
        }


async def main():
    print("\n" + "="*80)
    print("WebFetch 三级降级链路测试")
    print(f"测试用例数: {len(TEST_WEBSITES)}")
    print("="*80)

    coordinator = FetchCoordinator(FetchCoordinatorConfig(
        steel_base_url="http://localhost:3000",
        min_content_length=400,
        last_resort_min_length=50,
        static_timeout=15.0,
        browser_timeout=60.0,
    ))

    results = []
    for entry in TEST_WEBSITES:
        name, url, expected = entry[0], entry[1], entry[2] if len(entry) > 2 else True
        result = await test_website(coordinator, name, url, expected)
        results.append(result)
        await asyncio.sleep(1)

    print(f"\n\n{'='*80}")
    print("测试结果统计")
    print(f"{'='*80}")

    passed_count = sum(1 for r in results if r["passed"])
    total = len(results)
    print(f"总测试数: {total}")
    print(f"通过数:   {passed_count}")
    print(f"未通过数: {total - passed_count}")
    print(f"通过率:   {passed_count/total*100:.1f}%")

    print(f"\n{'='*80}")
    print("分类统计")
    print(f"{'='*80}")

    categories = [
        ("PDF 文档", 0, 6),
        ("DOCX 文档", 6, 8),
        ("XLSX 文档", 8, 11),
        ("PPTX 文档", 11, 14),
        ("XML/RSS", 14, 19),
        ("JSON/API", 19, 24),
        ("CSV/TXT/MD", 24, 29),
        ("静态 HTML", 29, 36),
        ("中文页面", 36, 46),
        ("SPA/JS", 46, 52),
        ("强反爬", 52, 57),
        ("HTTP 边界", 57, 63),
        ("真实404/重定向", 63, 67),
    ]
    for cat_name, start, end in categories:
        cat_results = results[start:end]
        cat_passed = sum(1 for r in cat_results if r["passed"])
        print(f"  {cat_name:12s}  {cat_passed}/{len(cat_results)} 通过")

    print(f"\n{'='*80}")
    print("详细结果")
    print(f"{'='*80}")
    for r in results:
        icon = "✅" if r["passed"] else "❌"
        expected_tag = "" if r["expected_success"] else " [期望失败]"
        length_info = f" ({r['length']} 字符)" if r["actual_success"] else ""
        print(f"{icon} {r['name']:25s}{expected_tag}{length_info}")


if __name__ == "__main__":
    asyncio.run(main())
