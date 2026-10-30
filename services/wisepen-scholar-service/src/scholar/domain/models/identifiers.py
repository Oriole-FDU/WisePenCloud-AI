"""外部论文标识的接入规范化。

身份只依据明确标识（arXiv / DOI / OpenAlex / S2 / CorpusId）判定。
容错解析，避免异常格式阻断离线同步与在线查询。
"""

import re
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import unquote, urlsplit

# arXiv ID：形如 ``2401.01234v2`` 或 ``hep-th/9901001``，可带版本号。
_ARXIV = re.compile(
    r"(?:\d{4}\.\d{4,5}|[a-z][a-z.\-]+/\d{7})(?:v\d+)?",
    re.IGNORECASE,
)

# DOI 后缀：以 ``10.<注册者>`` 开头，后接任意非空白字符。
_DOI = re.compile(r"10\.\d{4,9}/\S+", re.IGNORECASE)


class IdentifierKind(StrEnum):
    """论文标识种类。

    仅表示种类标签，不代表某个部署已具备对应的查询能力。
    """

    ARXIV = "arxiv"
    DOI = "doi"
    OPENALEX = "openalex"
    S2 = "s2"
    CORPUS_ID = "corpus_id"

    @classmethod
    def values(cls) -> list[str]:
        """返回所有枚举值的字符串列表"""
        return [item.value for item in cls]

    @classmethod
    def members(cls) -> list["IdentifierKind"]:
        """返回所有枚举成员本身的列表"""
        return list(cls)


@dataclass(frozen=True)
class PaperIdentifier:
    """规范化后的论文身份。"""

    kind: IdentifierKind
    value: str


def normalize_arxiv(value: str) -> str:
    """把 ``arxiv:ID``、arXiv URL 或裸 ID 规范化为裸 ID（小写、无版本号）。

    宽容解析各类格式，移除所有 fullmatch 强行报错，提取合法 ID 或优雅兜底。
    """
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()

    # URL 形式：剥离 host 与 /abs/、/pdf/ 前缀及 .pdf 后缀
    if value.lower().startswith(("https://", "http://")):
        parsed = urlsplit(value)
        value = re.sub(r"^/(abs|pdf)/", "", parsed.path)
        value = value.removesuffix(".pdf")

    # 允许带 ``arxiv:`` 前缀
    value = re.sub(r"^arxiv:\s*", "", value, flags=re.IGNORECASE).strip()

    # 尝试提取标准 arXiv ID 片段
    match = _ARXIV.search(value)
    if match:
        value = match.group(0)

    # 去掉版本号并统一为小写
    return re.sub(r"v\d+$", "", value, flags=re.IGNORECASE).lower()


def normalize_doi(value: str) -> str:
    """把 ``doi:``、DOI URL 或裸 DOI 规范化为小写 DOI 字符串。

    宽容解析各类格式，移除所有 fullmatch 强行报错，提取合法 DOI 或优雅兜底。
    """
    if not value or not isinstance(value, str):
        return ""
    value = value.strip()

    # URL 形式：剥离 host，并做一次 URL 解码
    if value.lower().startswith(("https://", "http://")):
        parsed = urlsplit(value)
        value = unquote(parsed.path.lstrip("/"))

    value = re.sub(r"^doi:\s*", "", value, flags=re.IGNORECASE).strip()

    # 尝试提取标准 10.xxxx/xxxx DOI 片段
    match = _DOI.search(value)
    if match:
        return match.group(0).lower().rstrip(".,;")

    return value.lower()


def parse_identifier(value: str) -> PaperIdentifier:
    """把常见外部标识形式解析为 ``PaperIdentifier``。

    依次尝试：URL（DOI / OpenAlex / S2）→ 裸 DOI → 命名空间前缀
    （``openalex:`` / ``s2:`` / ``corpusid:``）→ 兜底按 arXiv 处理。
    不再进行严苛强行报错，容错解析。
    """
    if not value or not isinstance(value, str):
        return PaperIdentifier(IdentifierKind.ARXIV, "")
    value = value.strip()

    # URL 形式：提取出对应的规范标识，再交由下方逻辑继续处理
    if value.lower().startswith(("http://", "https://")):
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if "doi.org" in host:
            return PaperIdentifier(IdentifierKind.DOI, normalize_doi(value))
        if "openalex.org" in host:
            value = "openalex:" + parsed.path.strip("/")
        elif "semanticscholar.org" in host:
            value = "s2:" + parsed.path.rstrip("/").rsplit("/", 1)[-1]

    # 裸 DOI：形如 ``doi:xxx`` 或直接以 ``10.`` 开头
    if value.lower().startswith("doi:") or value.startswith("10."):
        return PaperIdentifier(IdentifierKind.DOI, normalize_doi(value))

    # 带命名空间前缀的标识：``openalex:`` / ``s2:`` / ``corpusid:``
    for prefix, kind, pattern in (
            ("openalex:", IdentifierKind.OPENALEX, r"W[0-9]+"),
            ("s2:", IdentifierKind.S2, r"[a-f0-9]{40}"),
            ("corpusid:", IdentifierKind.CORPUS_ID, r"[0-9]+"),
    ):
        if not value.lower().startswith(prefix):
            continue
        identifier = value[len(prefix):].strip()
        match = re.search(pattern, identifier, re.IGNORECASE)
        matched_id = match.group(0) if match else identifier
        normalized = (
            matched_id.upper()
            if kind is IdentifierKind.OPENALEX
            else matched_id.lower()
        )
        return PaperIdentifier(kind, normalized)

    # 兜底：按 arXiv ID 处理（同时兼容 arXiv URL 形式）
    return PaperIdentifier(IdentifierKind.ARXIV, normalize_arxiv(value))
