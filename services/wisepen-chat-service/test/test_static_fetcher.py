"""
StaticFetcher 测试
"""
import asyncio
import sys
from pathlib import Path
import httpx
from typing import Optional


common_dir = Path(__file__).parent.parent.parent / "wisepen-common" / "src"
sys.path.insert(0, str(common_dir))

# 直接引入 common.logger
from common.logger import log_fail, log_error


class StaticFetcher:
    def __init__(self, timeout: float = 10.0, max_retries: int = 3):
        self.timeout = timeout
        self.max_retries = max_retries
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                          "AppleWebKit/537.36 (KHTML, like Gecko) "
                          "Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        }

    async def fetch(self, url: str) -> Optional[str]:
        """从静态资源获取 HTML 内容"""
        try:
            transport = httpx.AsyncHTTPTransport(retries=self.max_retries)
            async with httpx.AsyncClient(
                timeout=self.timeout,
                headers=self.headers,
                transport=transport,
                follow_redirects=True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text

        except httpx.TimeoutException:
            log_fail("静态抓取", f"请求超时 {self.timeout}s", url=url)
            return None

        except httpx.ConnectError:
            log_fail("静态抓取", "连接失败", url=url)
            return None

        except httpx.HTTPStatusError as e:
            log_fail("静态抓取", f"HTTP {e.response.status_code}", url=url)
            return None

        except httpx.RequestError:
            log_fail("静态抓取", "请求异常", url=url)
            return None

        except Exception as e:
            log_error("静态抓取", e, url=url)
            return None


async def fetch_and_print(url: str):
    print(f"Testing {url}...")
    fetcher = StaticFetcher()
    result = await fetcher.fetch(url)
    
    if result:
        print(f"Success! Content length: {len(result)}")
        print(f"\n==== HTML CONTENT (first 500 chars) ====\n")
        print(result[:500])
        print(f"\n==== END ====")
    else:
        print("Failed!")
    return result


if __name__ == "__main__":
    # 测试不同的网址
    test_url = "https://example.com"
    # test_url = "https://arxiv.org/abs/2310.06825"
    asyncio.run(fetch_and_print(test_url))
