"""摄入与内容存储共享的文档、结构和分块事实，不依赖执行阶段。"""

from dataclasses import dataclass, field
from hashlib import sha256

from common.utils.markdown import Anchor, Section


@dataclass(frozen=True, slots=True)
class ContentRevision:
    """完整原文与上游版本的不可变身份；不包含 metadata 或构建配置。"""

    resource_id: str
    document_version: int
    content_sha256: str

    @classmethod
    def create(
        cls, *, resource_id: str, document_version: int, text: str
    ) -> "ContentRevision":
        # 原文空白和 Unicode 编码都是内容身份的一部分，不进行规范化。
        return cls(
            resource_id, document_version, sha256(text.encode("utf-8")).hexdigest()
        )

    @property
    def content_revision(self) -> str:
        return f"{self.resource_id}@{self.document_version}#{self.content_sha256}"


@dataclass(slots=True)
class DocumentStructure:
    """复用 Common 结构模型，保留原文范围与章节树。"""

    total_length: int  # 完整 text 字段的 Python 字符数。
    sections: list[Section] = field(default_factory=list)
    anchors: list[Anchor] = field(default_factory=list)


@dataclass(slots=True)
class Document:
    """一次摄入的文档事实；ACL 由独立的资源授权模型拥有。"""

    resource_id: str
    document_version: int  # 上游文档版本，不替代内容 revision。
    content_revision: str
    text: str
    structure: DocumentStructure
    metadata: dict[str, object] = field(default_factory=dict)


@dataclass(slots=True)
class Chunk:
    """内容 revision 内的分块事实；Transformer 可以原地增强 metadata。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None  # 当前资源与 revision 下的本地章节 ID。
    section_path: list[str]
    raw_text: str  # 唯一正文事实，上下文不写回正文。
    content_token_count: int  # Common 对正文的 token 计数，不包含派生上下文。
    anchor_labels: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = str(self.metadata.get("contextual_context") or "").strip()
        return f"{context}\n\n{self.raw_text}" if context else self.raw_text
