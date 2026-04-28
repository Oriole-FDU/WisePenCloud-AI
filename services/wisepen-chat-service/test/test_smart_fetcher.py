"""
SmartFetcher 多网站极限测试
"""
import asyncio
import sys
from pathlib import Path

src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

common_dir = Path(__file__).parent.parent.parent / "wisepen-common" / "src"
sys.path.insert(0, str(common_dir))

from chat.application.browse_url.smart_fetcher import SmartFetcher

STEEL_BASE_URL = "http://localhost:3000"

TEST_URLS = [
    ("arXiv 论文", "https://arxiv.org/abs/2401.00001"),
    ("B站视频", "https://www.bilibili.com/video/BV1GJ411x7h7"),
    ("知乎问题", "https://www.zhihu.com/question/264939498"),
    ("微博热搜", "https://s.weibo.com/top/summary"),
    ("GitHub 仓库", "https://github.com/microsoft/vscode"),
    ("Reddit", "https://www.reddit.com/r/programming/"),
    ("Twitter/X", "https://x.com/elonmusk"),
    ("Medium 文章", "https://medium.com/"),
    ("Wikipedia", "https://en.wikipedia.org/wiki/Python_(programming_language)"),
    ("淘宝商品", "https://item.taobao.com/item.htm?id=678901234567"),
    ("京东商品", "https://item.jd.com/100012043978.html"),
    ("抖音", "https://www.douyin.com/"),
]


async def test_all():
    fetcher = SmartFetcher(steel_base_url=STEEL_BASE_URL)

    results = []
    for name, url in TEST_URLS:
        print(f"\n{'='*60}")
        print(f"Testing: {name} - {url}")
        print(f"{'='*60}")
        try:
            result = await fetcher.fetch(url)
            if result:
                status = "SUCCESS"
                length = len(result)
                preview = result[:200].replace('\n', ' ')
            else:
                status = "FAILED"
                length = 0
                preview = ""
        except Exception as e:
            status = "ERROR"
            length = 0
            preview = str(e)[:200]

        results.append((name, url, status, length, preview))
        print(f"  -> {status} | {length} chars | {preview[:100]}")

    print(f"\n\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"{'Site':<15} {'Status':<10} {'Length':<10} {'URL'}")
    print(f"{'-'*15} {'-'*10} {'-'*10} {'-'*40}")
    for name, url, status, length, _ in results:
        print(f"{name:<15} {status:<10} {length:<10} {url}")

    success_count = sum(1 for r in results if r[2] == "SUCCESS")
    print(f"\nTotal: {len(results)} | Success: {success_count} | Failed: {len(results) - success_count}")


if __name__ == "__main__":
    asyncio.run(test_all())
