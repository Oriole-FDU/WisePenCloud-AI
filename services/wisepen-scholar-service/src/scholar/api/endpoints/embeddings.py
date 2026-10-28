"""内部查询编码 HTTP 路由；保持 OpenAI embeddings 协议供公共客户端使用。"""

from __future__ import annotations

import base64
import struct
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator

from scholar.core.embedding_runtime import QUERY_MODEL

router = APIRouter(tags=["embeddings"])


class EmbeddingRequest(BaseModel):
    model: str
    input: str | list[str]
    dimensions: Literal[768] = 768
    encoding_format: Literal["float", "base64"] = "float"

    @field_validator("input")
    @classmethod
    def require_text(cls, value):
        values = [value] if isinstance(value, str) else value
        if not values or any(not text.strip() for text in values):
            raise ValueError("embedding input must contain non-empty text")
        return value


@router.post("/v1/embeddings")
def embeddings(payload: EmbeddingRequest, request: Request):
    if payload.model != QUERY_MODEL:
        raise HTTPException(422, "Unsupported embedding model")
    inputs = [payload.input] if isinstance(payload.input, str) else payload.input
    vectors = request.app.state.encoder.encode(inputs)
    if payload.encoding_format == "base64":
        # OpenAI SDK 默认请求 base64；公共 EmbeddingClient 会还原 float32。
        vectors = [
            base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode() for v in vectors
        ]
    return {
        "object": "list",
        "model": QUERY_MODEL,
        "data": [
            {"object": "embedding", "index": i, "embedding": vector}
            for i, vector in enumerate(vectors)
        ],
    }


@router.get("/health")
def health():
    return {"status": "ready", "model": QUERY_MODEL}
