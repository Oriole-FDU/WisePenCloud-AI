"""Crossref 仅解析完整标题到 DOI；论文详情继续由既有 lookup 获取。"""

import asyncio

import httpx

from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.interfaces.title_resolver import TitleMatch
from scholar.utils import normalize_title


class CrossrefTitleResolver:
    """完整标题 → DOI 的轻量解析；只返回 DOI，不展开论文详情。"""

    def __init__(self, http: httpx.AsyncClient, email: str):
        self.http, self.email = http, email
        # 单例持有信号量；所有请求共享两个槽位，等待也计入整次 match 的预算。
        self.semaphore = asyncio.Semaphore(2)

    async def match(
        self,
        titles: list[str],
        *,
        deadline: float | None = None,
    ) -> list[TitleMatch]:
        """并发解析多个标题；每项独立返回成功或失败。"""
        tasks = [asyncio.create_task(self._match_one(title, deadline)) for title in titles]
        try:
            return list(await asyncio.gather(*tasks))
        finally:
            # 取消或程序错误传播时，不能留下仍占用 HTTP 槽位的请求。
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _match_one(
        self,
        title: str,
        deadline: float | None,
    ) -> TitleMatch:
        try:
            async with asyncio.timeout_at(deadline):
                async with self.semaphore:
                    response = await self.http.get(
                        "https://api.crossref.org/works",
                        params={
                            "query.bibliographic": title,
                            "select": "DOI,title",
                            "rows": 5,
                            "mailto": self.email,
                        },
                        headers={
                            "User-Agent": (
                                f"WisePenScholar/1.0 (mailto:{self.email})"
                            ),
                            "Accept": "application/json",
                        },
                    )
                    response.raise_for_status()
                    works = response.json()["message"]["items"]
        except (httpx.HTTPError, TimeoutError) as error:
            # 包括 429 在内的外部失败逐项返回，不重试、不伪装成标题不存在。
            return TitleMatch(
                error=f"crossref: {str(error) or type(error).__name__}"
            )

        # 只保留标题归一化后完全匹配的 DOI；Crossref 的模糊召回不足以作为身份。
        target = normalize_title(title)
        dois = dict.fromkeys(
            parse_identifier(work["DOI"])
            for work in works
            if work.get("DOI")
            and any(
                normalize_title(value) == target
                for value in work.get("title", [])
            )
        )
        return TitleMatch([[doi] for doi in dois])
