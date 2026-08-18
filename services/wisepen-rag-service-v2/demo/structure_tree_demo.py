"""运行 sectioned/flat text 的真实索引构造与 READ outline 投影。"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from _demo_documents import (
    DemoDocument,
    build_demo_document,
    flat_text_markdown,
    sectioned_markdown,
)

from common.utils.document import OutlineAssembler, OutlineNode
from rag.api.schemas import DocumentOutlineResponse
from rag.application.rag.acl import PermissionAuthorizer
from rag.application.rag.read.outline import DocumentOutlineReader
from rag.domain.models.acl import PermissionScope, ResourceAcl
from rag.domain.repositories.mongo.published_resource_reader import (
    PublishedDocumentOutline,
)


class _DemoAclStore:
    async def get_resource_acl(self, resource_id: str) -> ResourceAcl:
        return ResourceAcl(
            resource_id=resource_id,
            acl_revision=1,
            owner_id="demo-reviewer",
        )


class _DemoStructureReader:
    """模拟 Mongo applied 读取，输入仍是生产构造器生成的结构事实。"""

    def __init__(self, documents: list[DemoDocument]) -> None:
        self._documents = {document.resource_id: document for document in documents}

    async def get_document_outline(
        self,
        resource_id: str,
    ) -> PublishedDocumentOutline | None:
        document = self._documents.get(resource_id)
        if document is None:
            return None
        return PublishedDocumentOutline(
            resource_id=document.resource_id,
            content_revision=document.revision.content_revision,
            document_version=document.revision.document_version,
            total_length=document.structure.total_length,
            outline=OutlineAssembler().assemble(
                sections=document.structure.sections,
                pages=document.structure.pages,
                anchors=document.structure.anchors,
            ),
        )


async def main() -> None:
    sectioned = build_demo_document(
        resource_id="demo-rain-garden",
        markdown=sectioned_markdown(),
    )
    flat_text = build_demo_document(
        resource_id="demo-orchard-frost-log",
        markdown=flat_text_markdown(),
    )
    reader = DocumentOutlineReader(
        structure_reader=_DemoStructureReader([sectioned, flat_text]),
        authorizer=PermissionAuthorizer(local_store=_DemoAclStore()),
    )
    scope = PermissionScope(user_id="demo-reviewer")
    sectioned_outline = await reader.get_document_outline(
        resource_id=sectioned.resource_id,
        permission_scope=scope,
    )
    flat_outline = await reader.get_document_outline(
        resource_id=flat_text.resource_id,
        permission_scope=scope,
    )

    output = "\n".join(
        [
            "=== Review notes ===",
            "- 两种文档都经过生产 INDEX 构造器，再经过 DocumentOutlineReader。",
            "- outline 节点保留 title，父子关系由 children 表达；不暴露 level 与 section_path。",
            "- 无标题、无页标记的纯文本仍保留 synthetic Section，且不伪造 page_range。",
            "",
            *_document_output("SECTIONED", sectioned, sectioned_outline.outline),
            "",
            *_document_output("FLAT_TEXT", flat_text, flat_outline.outline),
        ]
    )
    output_path = Path(__file__).with_name("structure_tree_demo_output.txt")
    output_path.write_text(output, encoding="utf-8")
    print(output_path)


def _document_output(
    label: str,
    document: DemoDocument,
    outline: list[OutlineNode],
) -> list[str]:
    payload = DocumentOutlineResponse(
        resource_id=document.resource_id,
        document_version=document.revision.document_version,
        content_revision=document.revision.content_revision,
        total_length=document.structure.total_length,
        outline=outline,
    ).model_dump(mode="json", exclude_none=True)
    return [
        f"=== {label} source text ===",
        document.markdown,
        f"=== {label} index summary ===",
        json.dumps(
            {
                "structure_mode": document.structure.mode.value,
                "section_count": len(document.sections),
                "reading_block_count": len(document.reading_blocks),
                "retrieval_chunk_count": len(document.retrieval_chunks),
            },
            ensure_ascii=False,
            indent=2,
        ),
        f"=== {label} getDocumentOutline ===",
        json.dumps(payload, ensure_ascii=False, indent=2),
    ]


if __name__ == "__main__":
    asyncio.run(main())
