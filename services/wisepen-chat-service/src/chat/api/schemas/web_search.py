import time
from typing import List, Optional

from pydantic import BaseModel, Field


class ImageResult(BaseModel):
    """图片搜索结果"""
    url: str = Field(..., description="图片 URL")
    desc: Optional[str] = Field(None, description="图片描述")

    class Config:
        extra = "ignore"


class SearchResult(BaseModel):
    """单条搜索结果"""
    title: str = Field(..., description="搜索结果标题")
    url: str = Field(..., description="搜索结果URL")
    snippet: str = Field(..., description="搜索结果摘要")
    images: List[ImageResult] = Field(default_factory=list, description="图片搜索结果")

    # 忽略结果中的未定义字段
    class Config:
        extra = "ignore"


class WebSearchResponse(BaseModel):
    """联网搜索响应"""
    query: str = Field(..., description="搜索查询")
    results: List[SearchResult] = Field(default_factory=list, description="搜索结果列表")
    answer: Optional[str] = Field(default=None, description="综合回答")
    
    # 用于 ttl 缓存
    searched_at: float = Field(default_factory=time.time, description="搜索时间戳")
