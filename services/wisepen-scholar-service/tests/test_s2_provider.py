"""S2 Provider 的字段选择、事实投影与传输边界行为。"""

import asyncio
from datetime import date

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from scholar.core.s2_transport import S2Transport
from scholar.domain.interfaces.paper_enricher import PaperEnrichment
from scholar.domain.interfaces.provider_error import ProviderError
from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.models.paper import PaperSource
from scholar.providers.s2 import S2Provider


class Permits:
    def __init__(self, delay=0):
        self.delay = delay

    async def eval(self, script, key_count, *args):
        if key_count == 3 and args[2].endswith(":sequence"):
            return [1, 0]
        return self.delay


def paper_response():
    return {
        "paperId": "a" * 40,
        "corpusId": 123,
        "externalIds": {"ArXiv": "2310.08560v2", "DOI": "10.5555/S2.TEST"},
        "url": "https://www.semanticscholar.org/paper/example",
        "title": "S2 paper",
        "venue": "Example Conference",
        "abstract": "A short abstract.",
        "authors": [{"name": "Jane Doe"}],
        "year": 2024,
        "publicationDate": "2024-01-02",
        "citationCount": 0,
        "influentialCitationCount": 2,
        "isOpenAccess": False,
        "tldr": {"model": "tldr@v2", "text": ""},
        "openAccessPdf": {"url": "https://example.test/paper.pdf"},
        "citationStyles": {"bibtex": "@article{example}"},
    }


@pytest.mark.asyncio
async def test_provider_selects_fields_projects_paper_and_uses_shared_transport():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[paper_response(), None])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        transport = S2Transport(http, Permits(), "platform-key")
        provider = S2Provider(transport)
        results = await provider.enrich(
            [
                parse_identifier("2310.08560"),
                parse_identifier("corpusid:123"),
            ],
            include_details=True,
        )

    request = requests[0]
    assert "user-agent" not in request.headers
    assert request.headers["x-api-key"] == "platform-key"
    assert request.headers["content-type"] == "application/json"
    assert request.read() == b'{"ids":["arxiv:2310.08560","CorpusId:123"]}'
    fields = set(request.url.params["fields"].split(","))
    assert {
        "paperId",
        "corpusId",
        "externalIds",
        "url",
        "title",
        "tldr",
        "citationCount",
        "influentialCitationCount",
        "isOpenAccess",
        "openAccessPdf",
        "citationStyles",
        "abstract",
        "authors",
        "year",
        "publicationDate",
        "venue",
    } <= fields
    assert not any(field.startswith("authors.") for field in fields)
    assert not fields.intersection(
        {"embedding", "referenceCount", "fieldsOfStudy", "s2FieldsOfStudy"}
    )

    result = results[0]
    assert isinstance(result, PaperEnrichment)
    assert results[1] is None
    assert set(result.metadata) == {"s2"}
    assert result.metadata["s2"]["citation_count"] == 0
    assert result.metadata["s2"]["influential_citation_count"] == 2
    assert result.metadata["s2"]["is_open_access"] is False
    assert result.metadata["s2"]["tldr"] == {"model": "tldr@v2", "text": ""}
    assert result.metadata["s2"]["open_access_pdf"] == "https://example.test/paper.pdf"
    assert result.metadata["s2"]["bibtex"] == "@article{example}"
    assert result.metadata["s2"]["venue"] == "Example Conference"
    assert "observed_at" in result.metadata["s2"]
    assert parse_identifier("2310.08560") in result.identifiers
    assert parse_identifier("10.5555/s2.test") in result.identifiers
    assert result.paper.source == PaperSource.S2
    assert result.paper.title == "S2 paper"
    assert result.paper.arxiv_id == "2310.08560"
    assert result.paper.doi == "10.5555/s2.test"
    assert result.paper.publication_date == date(2024, 1, 2)
    assert result.paper.pdf_url == "https://example.test/paper.pdf"


@pytest.mark.asyncio
async def test_enrich_without_details_does_not_request_or_return_a_full_paper():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "citationCount": 0,
                    "influentialCitationCount": 0,
                    "isOpenAccess": False,
                    "tldr": {"model": "tldr@v2", "text": ""},
                }
            ],
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = S2Provider(S2Transport(http, Permits(), "key"))
        result = await provider.enrich([parse_identifier("2310.08560")])

    fields = set(requests[0].url.params["fields"].split(","))
    assert not fields.intersection({"abstract", "authors", "year", "publicationDate"})
    assert result[0].paper is None
    assert result[0].metadata["s2"]["citation_count"] == 0
    assert result[0].metadata["s2"]["is_open_access"] is False
    assert result[0].metadata["s2"]["tldr"]["text"] == ""


@pytest.mark.asyncio
async def test_cancel_redis_failure_and_http_failure_propagate_without_bypassing_transport():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(429, json={"message": "limited"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        delayed = S2Provider(S2Transport(http, Permits(delay=10000), "key"))
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await delayed.enrich([parse_identifier("2310.08560")])

        class Unavailable:
            async def eval(self, *args):
                raise RedisConnectionError("unavailable")

        blocked = S2Provider(S2Transport(http, Unavailable(), "key"))
        with pytest.raises(ProviderError) as redis_error:
            await blocked.enrich([parse_identifier("2310.08560")])
        assert str(redis_error.value) == "unavailable"
        assert isinstance(redis_error.value.__cause__, RedisConnectionError)

        limited = S2Provider(S2Transport(http, Permits(), "key"))
        with pytest.raises(ProviderError) as http_error:
            await limited.enrich([parse_identifier("2310.08560")])
        assert isinstance(http_error.value.__cause__, httpx.HTTPStatusError)
        assert str(http_error.value) == str(http_error.value.__cause__)
        assert "429" in str(http_error.value)
        assert "api.semanticscholar.org" in str(http_error.value)
        assert "key" not in str(http_error.value)

    assert len(requests) == 1
