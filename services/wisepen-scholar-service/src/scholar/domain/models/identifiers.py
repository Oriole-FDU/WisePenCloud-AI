"""外部论文标识的接入规范化；身份只按明确标识，不做标题猜测。"""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlsplit

_ARXIV = re.compile(r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?", re.IGNORECASE)
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


class IdentifierKind(StrEnum):
    ARXIV = "arxiv"
    DOI = "doi"
    OPENALEX = "openalex"
    S2 = "s2"
    CORPUS_ID = "corpus_id"


@dataclass(frozen=True)
class PaperIdentifier:
    """规范化论文身份；身份类型不代表某个部署已经具备对应查询能力。"""

    kind: IdentifierKind
    value: str


def normalize_arxiv(value: str) -> str:
    value = value.strip()
    if value.lower().startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if parsed.hostname not in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
            raise ValueError("not an arXiv URL")
        value = re.sub(r"^/(abs|pdf)/", "", parsed.path)
        value = value.removesuffix(".pdf")
    value = re.sub(r"^arxiv:", "", value, flags=re.IGNORECASE)
    if not _ARXIV.fullmatch(value):
        raise ValueError("invalid arXiv identifier")
    return re.sub(r"v\d+$", "", value, flags=re.IGNORECASE).lower()


def normalize_doi(value: str) -> str:
    value = value.strip()
    if value.lower().startswith(("https://", "http://")):
        parsed = urlsplit(value)
        if parsed.hostname not in ("doi.org", "dx.doi.org"):
            raise ValueError("not a DOI URL")
        value = unquote(parsed.path.lstrip("/"))
    value = re.sub(r"^doi:", "", value, flags=re.IGNORECASE).strip()
    if not _DOI.fullmatch(value):
        raise ValueError("invalid DOI")
    return value.lower()


def parse_identifier(value: str) -> PaperIdentifier:
    value = value.strip()
    if value.lower().startswith(("http://", "https://")):
        parsed = urlsplit(value)
        if parsed.hostname in ("doi.org", "dx.doi.org"):
            return PaperIdentifier(IdentifierKind.DOI, normalize_doi(value))
        if parsed.hostname == "openalex.org":
            value = "openalex:" + parsed.path.strip("/")
        elif parsed.hostname in ("www.semanticscholar.org", "semanticscholar.org"):
            value = "s2:" + parsed.path.rstrip("/").rsplit("/", 1)[-1]
    if value.lower().startswith("doi:") or value.startswith("10."):
        return PaperIdentifier(IdentifierKind.DOI, normalize_doi(value))
    for prefix, kind, pattern in (
        ("openalex:", IdentifierKind.OPENALEX, r"W[0-9]+"),
        ("s2:", IdentifierKind.S2, r"[a-f0-9]{40}"),
        ("corpusid:", IdentifierKind.CORPUS_ID, r"[0-9]+"),
    ):
        if value.lower().startswith(prefix):
            identifier = value[len(prefix) :]
            if not re.fullmatch(pattern, identifier, re.IGNORECASE):
                raise ValueError("invalid namespaced paper identifier")
            normalized = (
                identifier.upper()
                if kind == IdentifierKind.OPENALEX
                else identifier.lower()
            )
            return PaperIdentifier(kind, normalized)
    return PaperIdentifier(IdentifierKind.ARXIV, normalize_arxiv(value))
