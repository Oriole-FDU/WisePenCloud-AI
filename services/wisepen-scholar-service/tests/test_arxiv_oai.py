import httpx
import pytest

from scholar.etl import arxiv_oai
from scholar.etl.arxiv_oai import (
    ArxivOaiHarvester,
    ArxivOaiProtocolError,
)

OAI = "http://www.openarchives.org/OAI/2.0/"
RAW = "http://arxiv.org/OAI/arXivRaw/"


@pytest.fixture(autouse=True)
def skip_real_wait(monkeypatch):
    async def no_wait(_seconds):
        pass

    monkeypatch.setattr(arxiv_oai, "sleep", no_wait)


@pytest.mark.asyncio
async def test_list_records_follows_opaque_token_and_keeps_raw_metadata_and_deletion(
    monkeypatch,
):
    requests = []
    request_times = []
    elapsed = 0
    waits = []
    pages = [
        f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-22T23:50:00Z</responseDate>
  <ListRecords>
    <record>
      <header><identifier>oai:arXiv.org:1706.03762</identifier><datestamp>2026-10-20</datestamp></header>
      <metadata>
        <arXivRaw xmlns="{RAW}">
          <id>1706.03762</id><submitter>Llion Jones</submitter>
          <version version="v1"><date>Mon, 12 Jun 2017 17:57:34 GMT</date><size>1102kb</size></version>
          <version version="v2"><date>Mon, 19 Jun 2017 16:49:45 GMT</date><size>1124kb</size></version>
          <title>Attention Is All You Need</title>
          <authors>Ashish Vaswani, Noam Shazeer, Niki Parmar</authors>
          <categories>cs.CL cs.LG</categories><doi>10.48550/arXiv.1706.03762</doi>
          <license>http://arxiv.org/licenses/nonexclusive-distrib/1.0/</license>
        </arXivRaw>
      </metadata>
    </record>
    <resumptionToken>opaque&amp;cursor=2</resumptionToken>
  </ListRecords>
</OAI-PMH>""",
        f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-23T00:02:00Z</responseDate>
  <ListRecords>
    <record><header status="deleted"><identifier>oai:arXiv.org:0704.0001</identifier><datestamp>2026-10-22</datestamp></header></record>
    <resumptionToken />
  </ListRecords>
</OAI-PMH>""",
    ]

    async def advance_clock(seconds):
        nonlocal elapsed
        waits.append(seconds)
        elapsed += seconds

    monkeypatch.setattr(arxiv_oai, "sleep", advance_clock)

    async def handle(request):
        requests.append(request)
        request_times.append(elapsed)
        return httpx.Response(200, text=pages[len(requests) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        harvest = await ArxivOaiHarvester(http).list_records()

    first = requests[0].url.params
    assert first["verb"] == "ListRecords"
    assert first["metadataPrefix"] == "arXivRaw"
    assert first["from"] == "2026-10-18"
    assert "until" not in first
    assert requests[1].url.params["verb"] == "ListRecords"
    assert requests[1].url.params["resumptionToken"] == "opaque&cursor=2"
    assert set(requests[1].url.params) == {"verb", "resumptionToken"}
    assert waits == [3, 3]
    assert request_times[1] - request_times[0] >= 3

    assert harvest.response_date == "2026-10-23T00:02:00Z"
    active, deleted = harvest.records
    assert active.identifier == "oai:arXiv.org:1706.03762"
    assert active.datestamp == "2026-10-20"
    assert active.datestamp != "Mon, 19 Jun 2017 16:49:45 GMT"
    assert not active.deleted
    assert active.metadata is not None
    assert active.metadata.tag == f"{{{RAW}}}arXivRaw"
    assert active.metadata.findtext(f"{{{RAW}}}authors") == (
        "Ashish Vaswani, Noam Shazeer, Niki Parmar"
    )
    assert [
        version.findtext(f"{{{RAW}}}date")
        for version in active.metadata.findall(f"{{{RAW}}}version")
    ] == ["Mon, 12 Jun 2017 17:57:34 GMT", "Mon, 19 Jun 2017 16:49:45 GMT"]
    assert active.metadata.findtext(f"{{{RAW}}}doi") == "10.48550/arXiv.1706.03762"
    assert active.metadata.findtext(f"{{{RAW}}}categories") == "cs.CL cs.LG"
    assert active.metadata.findtext(f"{{{RAW}}}license") == (
        "http://arxiv.org/licenses/nonexclusive-distrib/1.0/"
    )
    assert deleted.deleted and deleted.metadata is None
    assert deleted.datestamp == "2026-10-22"


@pytest.mark.asyncio
async def test_incremental_from_uses_the_previous_terminal_server_response_date():
    requests = []
    responses = [
        f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-23T00:02:00Z</responseDate>
  <ListRecords><resumptionToken /></ListRecords>
</OAI-PMH>""",
        f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-24T00:02:00Z</responseDate>
  <ListRecords><resumptionToken /></ListRecords>
</OAI-PMH>""",
    ]

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, text=responses[len(requests) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        provider = ArxivOaiHarvester(http)
        previous = await provider.list_records()
        harvest = await provider.list_records(from_date=previous.next_from)

    assert previous.response_date == "2026-10-23T00:02:00Z"
    assert previous.next_from == "2026-10-23"
    assert requests[0].url.params["from"] == "2026-10-18"
    assert requests[1].url.params["from"] == previous.next_from
    assert "until" not in requests[1].url.params
    assert harvest.response_date == "2026-10-24T00:02:00Z"


@pytest.mark.asyncio
async def test_no_records_match_is_a_completed_empty_harvest():
    xml = f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-24T00:02:00Z</responseDate>
  <error code="noRecordsMatch">No records match the request.</error>
</OAI-PMH>"""

    async def handle(_request):
        return httpx.Response(200, text=xml)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        harvest = await ArxivOaiHarvester(http).list_records()

    assert harvest.records == ()
    assert harvest.response_date == "2026-10-24T00:02:00Z"


@pytest.mark.asyncio
async def test_expired_resumption_token_fails_the_whole_harvest_for_replay():
    requests = []
    pages = [
        f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-24T00:02:00Z</responseDate>
  <ListRecords><resumptionToken>expired-token</resumptionToken></ListRecords>
</OAI-PMH>""",
        f"""<OAI-PMH xmlns="{OAI}">
  <responseDate>2026-10-24T00:03:00Z</responseDate>
  <error code="badResumptionToken">The token has expired.</error>
</OAI-PMH>""",
    ]

    async def handle(request):
        requests.append(request)
        return httpx.Response(200, text=pages[len(requests) - 1])

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(ArxivOaiProtocolError) as exc_info:
            await ArxivOaiHarvester(http).list_records()

    assert exc_info.value.code == "badResumptionToken"
    assert len(requests) == 2
    assert requests[1].url.params["resumptionToken"] == "expired-token"


@pytest.mark.asyncio
async def test_http_429_exposes_retry_after_without_retrying():
    requests = []

    async def handle(request):
        requests.append(request)
        return httpx.Response(429, headers={"Retry-After": "37"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            await ArxivOaiHarvester(http).list_records()

    assert exc_info.value.response.status_code == 429
    assert exc_info.value.response.headers["Retry-After"] == "37"
    assert len(requests) == 1
