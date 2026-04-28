"""
SteelFetcher 单独测试
"""
import asyncio
import sys
from pathlib import Path

src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

common_dir = Path(__file__).parent.parent.parent / "wisepen-common" / "src"
sys.path.insert(0, str(common_dir))

from chat.application.browse_url.fetcher.steel_fetcher import SteelFetcher

STEEL_BASE_URL = "http://localhost:3000"


async def test_steel(url: str):
    print(f"Testing SteelFetcher with: {url}")
    print("=" * 60)

    fetcher = SteelFetcher(steel_base_url=STEEL_BASE_URL)
    result = await fetcher.fetch(url)

    if result:
        print(f"Success! Content length: {len(result)} chars")
        print(f"\n==== Content (first 800 chars) ====\n")
        print(result[:800])
        print(f"\n==== END ====")
    else:
        print("Failed! No content returned.")

    return result


if __name__ == "__main__":
    test_url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    asyncio.run(test_steel(test_url))
