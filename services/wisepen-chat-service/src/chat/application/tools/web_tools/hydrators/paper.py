from __future__ import annotations

from difflib import SequenceMatcher

import httpx

from .models import HydratedPaper, HydrationStatus

# --- 常量配置 ---
TITLE_EXACT_MATCH_THRESHOLD = 0.96
TITLE_PARTIAL_MATCH_THRESHOLD = 0.84
OPENALEX_SEARCH_CANDIDATES = 3


class PaperHydrator:
    """仅使用 OpenAlex 的内部论文补全服务。"""

    __slots__ = ("_api_key", "_base_url", "_client")

    def __init__(
            self,
            *,
            http_client: httpx.AsyncClient,
            api_key: str,
            base_url: str,
    ) -> None:
        self._client = http_client
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    async def hydrate(
            self,
            *,
            doi: str | None = None,
            openalex_id: str | None = None,
            title: str | None = None,
            candidate_title: str | None = None,
    ) -> HydratedPaper:
        try:
            # 1. 路由分发
            if openalex_id:
                return await self._hydrate_by_openalex_id(openalex_id)
            if doi:
                return await self._hydrate_by_doi(doi)
            if search_title := (title or candidate_title):
                return await self._hydrate_by_title(search_title)

            return HydratedPaper(status=HydrationStatus.NOT_FOUND, failure_reason="missing_lookup_key")

        except httpx.HTTPStatusError as exc:
            # 2. 状态码显式分支匹配
            match exc.response.status_code:
                case 404:
                    return HydratedPaper(status=HydrationStatus.NOT_FOUND, failure_reason="openalex_not_found")
                case 429:
                    return HydratedPaper(status=HydrationStatus.FAILED, failure_reason="openalex_rate_limited")
                case code:
                    return HydratedPaper(status=HydrationStatus.FAILED, failure_reason=f"openalex_http_{code}")

        except httpx.HTTPError as exc:
            # 3. 其他网络异常
            return HydratedPaper(status=HydrationStatus.FAILED, failure_reason=type(exc).__name__)

    async def _hydrate_by_openalex_id(self, openalex_id: str) -> HydratedPaper:
        work_id = openalex_id.strip().rstrip("/")
        if work_id.lower().startswith(("https://openalex.org/", "openalex.org/")):
            work_id = work_id.rsplit("/", 1)[-1]

        response = await self._client.get(
            f"{self._base_url}/works/{work_id}",
            params={"api_key": self._api_key},
        )
        response.raise_for_status()

        return _paper_from_work(response.json(), HydrationStatus.HYDRATED)

    async def _hydrate_by_doi(self, doi: str) -> HydratedPaper:
        normalized_doi = doi.strip()
        lower = normalized_doi.lower()

        for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
            if lower.startswith(prefix):
                normalized_doi = normalized_doi[len(prefix):].strip()
                break

        response = await self._client.get(
            f"{self._base_url}/works/doi:{normalized_doi}",
            params={"api_key": self._api_key},
        )
        response.raise_for_status()

        return _paper_from_work(response.json(), HydrationStatus.HYDRATED)

    async def _hydrate_by_title(self, title: str) -> HydratedPaper:
        response = await self._client.get(
            f"{self._base_url}/works",
            params={
                "search": title,
                "per-page": OPENALEX_SEARCH_CANDIDATES,
                "api_key": self._api_key,
            },
        )
        response.raise_for_status()

        data = response.json()
        results = data.get("results") if isinstance(data, dict) else None
        if not results:
            return HydratedPaper(status=HydrationStatus.NOT_FOUND, failure_reason="openalex_empty_results")

        # 评分与过滤
        scored = [
            (score, item)
            for item in results
            if isinstance(item, dict)
               and (score := _title_similarity(title, str(item.get("display_name") or item.get("title") or ""))) > 0
        ]
        if not scored:
            return HydratedPaper(status=HydrationStatus.NOT_FOUND, failure_reason="openalex_empty_results")

        # 排序并提取最佳匹配
        scored.sort(key=lambda pair: pair[0], reverse=True)
        best_score, best = scored[0]

        # 阈值拦截
        if best_score < TITLE_PARTIAL_MATCH_THRESHOLD:
            return HydratedPaper(status=HydrationStatus.NOT_FOUND, failure_reason="openalex_low_similarity")

        # 决定匹配状态 (0.03 是明显领先第二名的经验阈值)
        is_exact = best_score >= TITLE_EXACT_MATCH_THRESHOLD and (
                len(scored) == 1 or (best_score - scored[1][0] >= 0.03)
        )
        status = HydrationStatus.HYDRATED if is_exact else HydrationStatus.PARTIAL

        return _paper_from_work(best, status)


def _paper_from_work(data: dict, status: HydrationStatus) -> HydratedPaper:
    primary_location = data.get("primary_location") or {}
    source = primary_location.get("source") or {}
    open_access = data.get("open_access") or {}

    # 解析作者列表
    authors = tuple(
        name
        for item in (data.get("authorships") or ())
        if (name := item.get("author", {}).get("display_name"))
    )

    # 解析主题/概念并去重
    raw_topics = (data.get("topics") or data.get("concepts") or ())
    concepts_or_topics = tuple(
        dict.fromkeys(
            name for item in raw_topics if (name := item.get("display_name"))
        )
    )

    return HydratedPaper(
        status=status,
        title=data.get("display_name") or data.get("title"),
        authors=authors,
        year=data.get("publication_year"),
        venue=source.get("display_name") or primary_location.get("raw_source_name"),
        doi=data.get("doi"),
        openalex_id=data.get("id"),
        abstract=_abstract_from_inverted_index(data.get("abstract_inverted_index")),
        landing_url=primary_location.get("landing_page_url"),
        pdf_url=primary_location.get("pdf_url"),
        open_access=open_access.get("is_oa"),
        cited_by_count=data.get("cited_by_count"),
        concepts_or_topics=concepts_or_topics,
        source_updated_at=data.get("updated_date") or data.get("updated"),
    )


def _abstract_from_inverted_index(value: dict[str, list[int]] | None) -> str | None:
    """OpenAlex 把摘要存成 {词: [出现位置...]} 的倒排索引，需要按位置重新拼回原文。"""
    if not value:
        return None

    by_position = {
        position: word
        for word, positions in value.items()
        for position in positions
    }
    if not by_position:
        return None

    return " ".join(by_position[position] for position in sorted(by_position))


def _title_similarity(left: str, right: str) -> float:
    normalized_left = " ".join(left.casefold().strip().split())
    normalized_right = " ".join(right.casefold().strip().split())
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()
