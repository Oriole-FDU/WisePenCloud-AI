"""摄入 Chunk 合同与分块阶段职责；分块只确定正文边界、顺序和结构归属。"""

from dataclasses import dataclass, field
from typing import Protocol

from rag.core.schema import Document


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


class Chunker(Protocol):
    async def split(self, document: Document) -> list[Chunk]: ...
