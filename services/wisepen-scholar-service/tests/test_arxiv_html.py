"""HTML 离线边界：静默跳过、版本进度、真实失败传播和已保存正文的重放。"""

import httpx
import orjson
import pytest

from scholar.etl.arxiv import open_database
from scholar.etl.arxiv_html import import_arxiv_html
from scholar.etl.arxiv_html_cleaner import clean_arxiv_html

BODY = "A differentiable retrieval method preserves quantum graph structure. " * 15
HTML = f"""<article class="ltx_document"><h1 class="ltx_title_document">HTML title</h1>
<div class="ltx_abstract">HTML abstract</div><h2>Methods</h2>
<div class="ltx_para">{BODY}<cite>REFERENCE_SENTINEL</cite><math>MATH_SENTINEL</math></div>
<section class="ltx_bibliography">BIBLIOGRAPHY_SENTINEL</section></article>"""


@pytest.fixture
def db(tmp_path):
    with open_database(tmp_path / "papers.duckdb") as database:
        for identifier in ("2601.00001", "2601.00002", "2601.00003"):
            document = {
                "title": "Metadata title",
                "abstract": "Metadata abstract",
                "versions": [{"version": "v1"}],
                "metadata": {"local": {"kept": True}},
            }
            database.execute(
                "INSERT INTO arxiv VALUES (?,NULL,'{}',?::JSON)",
                [identifier, orjson.dumps(document).decode()],
            )
        yield database


def test_cleaner_keeps_body_without_repeated_metadata_or_dom_noise():
    result = clean_arxiv_html(HTML)
    assert result["title"] == "HTML title"
    assert result["abstract"] == "HTML abstract"
    assert "quantum graph" in result["body"]
    assert all(
        value not in result["body"]
        for value in (
            "HTML title",
            "HTML abstract",
            "REFERENCE_SENTINEL",
            "MATH_SENTINEL",
            "BIBLIOGRAPHY_SENTINEL",
        )
    )
    assert clean_arxiv_html("<html><body>No HTML rendition</body></html>") is None


async def test_network_resume_skips_completed_versions_and_publishes_saved_body(db):
    calls, published = [], {}

    def handler(request):
        calls.append(request.url.path)
        if request.url.path.endswith("00003v1"):
            return httpx.Response(200, text=HTML)
        if request.url.path.endswith("00002v1"):
            return httpx.Response(404)
        if calls.count(request.url.path) == 1:
            raise httpx.ConnectError("network disconnected", request=request)
        return httpx.Response(410)

    async def publish():
        for identifier, raw in db.execute(
            "SELECT arxiv_id,document FROM arxiv JOIN pending_publish USING(arxiv_id)"
        ).fetchall():
            published[identifier] = orjson.loads(raw)
        db.execute("DELETE FROM pending_publish")
        return {}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(httpx.ConnectError):
            await import_arxiv_html(db, http, publish=publish, request_interval=0)
        assert db.execute("SELECT arxiv_id FROM pending_publish").fetchall() == [
            ("2601.00003",)
        ]
        result = await import_arxiv_html(db, http, publish=publish, request_interval=0)
        assert (
            result["complete"] and result["available"] == 1 and result["skipped"] == 2
        )
        await import_arxiv_html(db, http, publish=publish, request_interval=0)
    assert calls == [
        "/html/2601.00003v1",
        "/html/2601.00002v1",
        "/html/2601.00001v1",
        "/html/2601.00001v1",
    ]
    assert "quantum graph" in published["2601.00003"]["full_text"]
    assert published["2601.00003"]["title"] == "Metadata title"
    assert published["2601.00003"]["metadata"] == {"local": {"kept": True}}


@pytest.mark.parametrize("code", [403, 429, 500])
async def test_http_failures_remain_pending_instead_of_becoming_unsupported(db, code):
    async def publish():
        return {}

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(code))
    ) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await import_arxiv_html(db, http, publish=publish, request_interval=0)
    assert db.execute("SELECT DISTINCT status FROM arxiv_html").fetchall() == [
        ("pending",)
    ]
    assert db.execute("SELECT count(*) FROM pending_publish").fetchone()[0] == 0


async def test_publish_failure_does_not_download_the_body_again(db):
    db.execute("DELETE FROM arxiv WHERE arxiv_id <> '2601.00003'")
    requests, publications = [], []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(200, text=HTML)

    async def publish():
        pending = db.execute("SELECT arxiv_id FROM pending_publish").fetchall()
        publications.append(pending)
        if len(publications) == 2:
            raise RuntimeError("ES disconnected")
        db.execute("DELETE FROM pending_publish")
        return {}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(RuntimeError, match="ES disconnected"):
            await import_arxiv_html(db, http, publish=publish, request_interval=0)
        result = await import_arxiv_html(db, http, publish=publish, request_interval=0)
    assert result["complete"] and result["available"] == 1
    assert requests == ["/html/2601.00003v1"]
    assert publications[-1] == [("2601.00003",)]


async def test_a_new_paper_version_is_fetched_after_previous_version_was_checked(db):
    db.execute("DELETE FROM arxiv WHERE arxiv_id <> '2601.00003'")
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(
            404 if request.url.path.endswith("v1") else 200, text=HTML
        )

    async def publish():
        db.execute("DELETE FROM pending_publish")
        return {}

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        await import_arxiv_html(db, http, publish=publish, request_interval=0)
        db.execute("""UPDATE arxiv SET document=json_merge_patch(document,
            '{"versions":[{"version":"v1"},{"version":"v2"}]}')""")
        result = await import_arxiv_html(db, http, publish=publish, request_interval=0)
    assert requests == ["/html/2601.00003v1", "/html/2601.00003v2"]
    assert result["available"] == 1 and result["skipped"] == 0
