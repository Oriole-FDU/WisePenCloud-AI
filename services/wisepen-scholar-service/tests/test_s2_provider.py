"""S2 批量查询的字段、完整 Paper 与传输边界合同。"""

import asyncio
from datetime import date, datetime

import httpx
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from scholar.domain.errors import ProviderError
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.models.identifiers import parse_identifier
from scholar.domain.models.paper import PaperSource
from scholar.providers.s2 import S2Provider, S2Transport


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
    }


@pytest.mark.asyncio
async def test_lookup_requests_fixed_fields_and_returns_complete_paper():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, json=[paper_response(), None])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        transport = S2Transport(http, Permits(), "platform-key")
        provider = S2Provider(transport)
        results = await provider.lookup(
            [parse_identifier("2310.08560"), parse_identifier("corpusid:123")]
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

    match = results[0]
    assert isinstance(match, PaperLookupMatch)
    assert results[1].papers == []
    paper = match.papers[0]
    assert paper.metadata["s2"]["citation_count"] == 0
    assert paper.metadata["s2"]["influential_citation_count"] == 2
    assert paper.metadata["s2"]["is_open_access"] is False
    assert paper.metadata["s2"]["tldr"] == {"model": "tldr@v2", "text": ""}
    assert paper.metadata["s2"]["open_access_pdf"] == "https://example.test/paper.pdf"
    assert "citationStyles" not in fields
    assert paper.metadata["s2"]["venue"] == "Example Conference"
    datetime.fromisoformat(paper.metadata["s2"]["observed_at"])
    assert paper.source == PaperSource.S2
    assert paper.title == "S2 paper"
    assert paper.authors == ["Jane Doe"]
    assert paper.arxiv_id == "2310.08560"
    assert paper.doi == "10.5555/s2.test"
    assert paper.publication_date == date(2024, 1, 2)
    assert paper.pdf_url == "https://example.test/paper.pdf"
    assert S2Provider.identifiers(paper_response()) == [
        parse_identifier(f"s2:{'a' * 40}"),
        parse_identifier("corpusid:123"),
        parse_identifier("2310.08560v2"),
        parse_identifier("10.5555/s2.test"),
    ]


@pytest.mark.parametrize(
    ("authors", "expected"),
    [([], []), ([{"name": "First"}, {"name": "Second"}], ["First", "Second"])],
)
@pytest.mark.asyncio
async def test_lookup_preserves_all_authors(authors, expected):
    payload = paper_response()
    payload["authors"] = authors

    async def handle(request):
        return httpx.Response(200, json=[payload])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = S2Provider(S2Transport(http, Permits(), "key"))
        result = await provider.lookup([parse_identifier("2310.08560")])

    assert result[0].papers[0].authors == expected


def test_metadata_only_keeps_venue_when_upstream_provided_it():
    payload = paper_response()
    payload.pop("venue")
    metadata = S2Provider.metadata(payload)

    assert "venue" not in metadata["s2"]
    assert metadata["s2"]["tldr"]["model"] == "tldr@v2"
    assert "observed_at" in metadata["s2"]


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
                await delayed.lookup([parse_identifier("2310.08560")])

        class Unavailable:
            async def eval(self, *args):
                raise RedisConnectionError("unavailable")

        blocked = S2Provider(S2Transport(http, Unavailable(), "key"))
        with pytest.raises(ProviderError) as redis_error:
            await blocked.lookup([parse_identifier("2310.08560")])
        assert str(redis_error.value) == "unavailable"
        assert isinstance(redis_error.value.__cause__, RedisConnectionError)

        limited = S2Provider(S2Transport(http, Permits(), "key"))
        with pytest.raises(ProviderError) as http_error:
            await limited.lookup([parse_identifier("2310.08560")])
        assert isinstance(http_error.value.__cause__, httpx.HTTPStatusError)
        assert str(http_error.value) == str(http_error.value.__cause__)
        assert "429" in str(http_error.value)
        assert "api.semanticscholar.org" in str(http_error.value)
        assert "key" not in str(http_error.value)

    assert len(requests) == 1
