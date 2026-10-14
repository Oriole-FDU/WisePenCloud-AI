"""摄入与内容存储共享的文档、结构和分块事实，不依赖执行阶段。"""

from dataclasses import dataclass, field
from hashlib import sha256

from common.utils.markdown import Anchor, Section


def rag_section_id(
    *, resource_id: str, content_revision: str, common_section_id: str
) -> str:
    """限定 Common 章节身份；目录、Chunk 与后续读取共用不透明 ID。"""
    identity = f"{resource_id}\0{content_revision}\0{common_section_id}"
    return f"rsec_{sha256(identity.encode('utf-8')).hexdigest()[:24]}"


@dataclass(frozen=True, slots=True)
class ContentRevision:
    """完整原文与上游版本的不可变身份；不包含 type、metadata 或构建配置。"""

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
    """复用 Common 结构模型与原文范围，章节及父章节 ID 已限定到资源/revision。"""

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
    type: str | None = None  # 摄入类别；None 使用默认 Pipeline，不参与内容身份。


@dataclass(slots=True)
class Chunk:
    """内容 revision 内的分块事实；Transformer 可以原地增强 metadata。"""

    chunk_id: str
    resource_id: str
    content_revision: str
    chunk_index: int
    section_id: str | None  # 与 Document 结构一致的 RAG 不透明章节 ID。
    section_path: list[str]
    raw_text: str  # 唯一正文事实，上下文不写回正文。
    content_token_count: int  # Common 对正文的 token 计数，不包含派生上下文。
    anchor_labels: list[str]
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def retrieval_text(self) -> str:
        context = self.metadata.get("contextual_context", "").strip()
        # 路径与生成上下文只服务索引；原文、offset 和正文计数保持不变。
        prefixes = [part for part in (">".join(self.section_path), context) if part]
        return "\n\n".join([*prefixes, self.raw_text])
