from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

import steel
from steel import AsyncSteel

from common.logger import log_fail, log_ok

__all__ = [
    "SteelFetcher",
    "SteelFetcherConfig",
]


@dataclass(frozen=True)
class SteelFetcherConfig:
    base_url: str
    timeout: float = 60.0
    max_retries: int = 2
    use_proxy: bool = False
    delay_ms: float = 0.0
    region: Optional[Dict[str, Any]] = None
    strip_output: bool = True


class SteelFetcher:
    """通过 Steel API 的 scrape 接口获取页面 Markdown 内容"""

    def __init__(self, config: SteelFetcherConfig):
        self._config = config
        self._client = AsyncSteel(
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
        )
        log_ok(
            "Steel 抓取器初始化",
            base_url=config.base_url,
            timeout=config.timeout,
            max_retries=config.max_retries,
            use_proxy=config.use_proxy,
            delay_ms=config.delay_ms,
        )

    async def fetch(self, url: str) -> Optional[str]:
        try:
            kwargs: Dict[str, Any] = {
                "url": url,
                "format": ["markdown"],
                "use_proxy": self._config.use_proxy,
            }

            if self._config.delay_ms > 0:
                kwargs["delay"] = self._config.delay_ms

            if self._config.region is not None:
                kwargs["region"] = self._config.region

            response = await self._client.scrape(**kwargs)

            markdown = response.content.markdown if response.content else None
            if markdown is None:
                log_fail("Steel 浏览器抓取", "响应中无 markdown 内容", url=url)
                return None

            if self._config.strip_output:
                markdown = markdown.strip()

            if not markdown:
                log_fail("Steel 浏览器抓取", "markdown 内容为空", url=url)
                return None

            return markdown

        except steel.RateLimitError as e:
            log_fail("Steel 浏览器抓取", f"触发限流: {e}", url=url)
            return None

        except steel.APITimeoutError as e:
            log_fail("Steel 浏览器抓取", f"请求超时: {e}", url=url)
            return None

        except steel.APIConnectionError as e:
            log_fail("Steel 浏览器抓取", f"连接失败: {e}", url=url)
            return None

        except steel.APIStatusError as e:
            log_fail(
                "Steel 浏览器抓取",
                f"HTTP {e.status_code}: {e.response}",
                url=url,
            )
            return None

        except steel.APIError as e:
            log_fail("Steel 浏览器抓取", f"Steel API 错误: {e}", url=url)
            return None

        except Exception as e:
            log_fail("Steel 浏览器抓取", e, url=url)
            return None

