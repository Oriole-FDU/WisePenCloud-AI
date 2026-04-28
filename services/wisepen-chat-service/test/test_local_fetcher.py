import asyncio
import sys
from pathlib import Path

src_dir = Path(__file__).parent.parent / "src"
sys.path.insert(0, str(src_dir))

common_dir = Path(__file__).parent.parent.parent / "wisepen-common" / "src"
sys.path.insert(0, str(common_dir))

from chat.application.browse_url.fetcher.local_fetcher import LocalScriptFetcher


async def fetch_and_print(url: str):
    print(f"Testing {url}...")
    fetcher = LocalScriptFetcher()
    result = await fetcher.fetch(url)
    
    if result:
        print(f"Success! Content length: {len(result)}")
        print(f"\n==== MARKDOWN CONTENT ====\n")
        print(result)
        print(f"\n==== END ====")
    else:
        print("Failed!")
    return result


if __name__ == "__main__":
    test_url = "https://www.bilibili.com/video/BV1GJ411x7h7"
    asyncio.run(fetch_and_print(test_url))
