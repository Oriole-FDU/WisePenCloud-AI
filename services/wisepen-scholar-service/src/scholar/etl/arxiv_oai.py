"""独立 ETL 的 arXiv OAI-PMH 增量收割步骤。"""

from asyncio import sleep
from dataclasses import dataclass
from xml.etree import ElementTree as ET

import httpx

_ENDPOINT = "https://oaipmh.arxiv.org/oai"
INITIAL_FROM = "2026-10-18"
_OAI_NS = "http://www.openarchives.org/OAI/2.0/"
_ARXIV_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"


@dataclass(frozen=True)
class ArxivOaiRecord:
    identifier: str
    datestamp: str
    deleted: bool
    metadata: ET.Element | None


@dataclass(frozen=True)
class ArxivOaiHarvest:
    response_date: str
    records: tuple[ArxivOaiRecord, ...]

    @property
    def next_from(self) -> str:
        # arXiv Identify 声明日级 granularity；按日期重放整日可覆盖同日后续修改。
        return self.response_date.partition("T")[0]


class ArxivOaiProtocolError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"OAI-PMH {code}: {message}")


class ArxivOaiHarvester:
    def __init__(self, http: httpx.AsyncClient):
        self.http = http

    async def list_records(self, *, from_date: str = INITIAL_FROM) -> ArxivOaiHarvest:
        # OAI-PMH 的续页请求只携带不透明 token；终页 responseDate 才能作为下一轮 from。
        params = {
            "verb": "ListRecords",
            "metadataPrefix": "arXivRaw",
            "from": from_date,
        }
        records: list[ArxivOaiRecord] = []
        is_initial_request = True

        while True:
            # arXiv 要求相邻 Legacy API 请求至少间隔 3 秒；分页串行复用同一客户端连接。
            await sleep(3)
            response = await self.http.get(_ENDPOINT, params=params)
            # 429/503 等 HTTP 状态交给调用方读取 Retry-After 并安排重试，本适配器不隐藏重试。
            response.raise_for_status()
            root = ET.fromstring(response.content)
            if root.tag != f"{{{_OAI_NS}}}OAI-PMH":
                raise ValueError("Response is not an OAI-PMH document")

            response_date = root.findtext(f"{{{_OAI_NS}}}responseDate")
            if response_date is None:
                raise ValueError("OAI-PMH response is missing responseDate")

            error = root.find(f"{{{_OAI_NS}}}error")
            if error is not None:
                code = error.attrib["code"]
                message = error.text or ""
                if code == "noRecordsMatch" and is_initial_request:
                    return ArxivOaiHarvest(response_date, ())
                # badResumptionToken 不返回已读页面的部分结果，调用方应从原确认游标重放。
                raise ArxivOaiProtocolError(code, message)

            page = root.find(f"{{{_OAI_NS}}}ListRecords")
            if page is None:
                raise ValueError("OAI-PMH response is missing ListRecords")

            for item in page.findall(f"{{{_OAI_NS}}}record"):
                header = item.find(f"{{{_OAI_NS}}}header")
                identifier = header.findtext(f"{{{_OAI_NS}}}identifier")
                datestamp = header.findtext(f"{{{_OAI_NS}}}datestamp")
                if identifier is None or datestamp is None:
                    raise ValueError(
                        "OAI-PMH record header is missing its identity or datestamp"
                    )

                deleted = header.attrib.get("status") == "deleted"
                metadata = None
                if not deleted:
                    metadata_container = item.find(f"{{{_OAI_NS}}}metadata")
                    metadata = metadata_container[0]
                    if metadata.tag != f"{{{_ARXIV_RAW_NS}}}arXivRaw":
                        raise ValueError(
                            "OAI-PMH record does not contain arXivRaw metadata"
                        )

                records.append(ArxivOaiRecord(identifier, datestamp, deleted, metadata))

            token_element = page.find(f"{{{_OAI_NS}}}resumptionToken")
            token = token_element.text if token_element is not None else None
            if token is None or not token.strip():
                return ArxivOaiHarvest(response_date, tuple(records))

            params = {"verb": "ListRecords", "resumptionToken": token}
            is_initial_request = False
