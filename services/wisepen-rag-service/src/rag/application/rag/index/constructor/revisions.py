from hashlib import sha256

from rag.domain.models.content import ContentRevision

# 当前内容索引 schema 版本，参与 content_revision 计算；
# 当索引规则（chunker、结构解析、字段集合）发生变化时递增，强制旧 revision 失效。
INDEX_SCHEMA_VERSION = "rag-v2-content:v7"


def build_content_revision_id(
    *,
    resource_id: str,
    document_version: int,
    markdown: str,
    index_schema_version: str = INDEX_SCHEMA_VERSION,
) -> str:
    """在结构解析前确定 revision，使所有派生身份使用同一命名空间。"""
    content_hash = sha256(markdown.encode("utf-8")).hexdigest()

    identity = "\0".join(
        (resource_id, str(document_version), content_hash, index_schema_version)
    )
    return f"rrev_{sha256(identity.encode('utf-8')).hexdigest()[:16]}"


def create_content_revision(
    *,
    resource_id: str,
    document_version: int,
    markdown: str,
    index_schema_version: str = INDEX_SCHEMA_VERSION,
) -> ContentRevision:
    """以资源、上游版本、原文和 schema 共同确定 revision 身份，
    在结构解析后调用，返回完整的领域对象，供后续发布流程使用。
    """
    # content_hash 仅用于内容比对（与 revision ID 用途不同），不参与身份计算。
    content_hash = sha256(markdown.encode("utf-8")).hexdigest()
    content_revision = build_content_revision_id(
        resource_id=resource_id,
        document_version=document_version,
        markdown=markdown,
        index_schema_version=index_schema_version,
    )
    return ContentRevision(
        resource_id=resource_id,
        content_revision=content_revision,
        document_version=document_version,
        content_hash=content_hash,
        index_schema_version=index_schema_version,
    )
