"""摄入文档的正文、内容版本和 Common Markdown 结构事实。"""

from dataclasses import dataclass, field

from common.utils.markdown import Anchor, Section


@dataclass(slots=True)
class DocumentStructure:
    """复用 Common 结构模型，保留原文范围与章节树。"""

    total_length: int  # 完整 raw_markdown 的 Python 字符数。
    sections: list[Section] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)


@dataclass(slots=True)
class Document:
    """一次摄入的文档事实；ACL 由独立的资源授权模型拥有。"""

    resource_id: str
    document_version: int  # 上游文档版本，不替代内容 revision。
    content_revision: str
    raw_markdown: str
    structure: DocumentStructure
    metadata: dict[str, object] = field(default_factory=dict)
