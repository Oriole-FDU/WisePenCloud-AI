"""Beanie 持久化实体。"""

from .documents import DocumentRevisionEntity, ResourceIndexStateEntity
from .resource_acl import ResourceAclEntity

__all__ = [
    "DocumentRevisionEntity",
    "ResourceAclEntity",
    "ResourceIndexStateEntity",
]
