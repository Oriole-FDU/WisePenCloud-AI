"""Mongo backend 实现。"""

from .acl import MongoResourceAclRepository
from .content import MongoContentStorage

__all__ = ["MongoContentStorage", "MongoResourceAclRepository"]
