"""Unpaywall DOI 查询；仅提取明确的 PDF URL，不使用 OA 状态或落地页。"""

import asyncio
from urllib.parse import quote

import httpx

from scholar.domain.errors import ProviderError, provider_error_message


class UnpaywallProvider:
    def __init__(self, http: httpx.AsyncClient, email: str):
        self.http, self.email = http, email
        self._slots = asyncio.Semaphore(4)

    async def recover(self, doi: str) -> str | None:
        try:
            async with self._slots:
                response = await self.http.get(
                    f"https://api.unpaywall.org/v2/{quote(doi, safe='/')}",
                    params={"email": self.email},
                    timeout=5,
                )
            if response.status_code == 404:
                return None
            response.raise_for_status()
        except httpx.HTTPError as error:
            raise ProviderError(provider_error_message(error)) from error

        payload = response.json()
        best = payload["best_oa_location"]
        if best and best.get("url_for_pdf"):
            return best["url_for_pdf"]
        return next(
            (location["url_for_pdf"] for location in payload["oa_locations"] if location.get("url_for_pdf")),
            None,
        )
