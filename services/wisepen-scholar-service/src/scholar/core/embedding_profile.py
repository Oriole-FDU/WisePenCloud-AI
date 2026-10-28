"""本地 SPECTER2 投影合同；在线查询与离线论文共享版本和输入规则。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

BASE_REVISION = "3447645e1def9117997203454fa4495937bfbd83"
DOCUMENT_REVISION = "2081559630a80fc5851d8f798a05ba81e9468089"
QUERY_REVISION = "3f4448817028388648a74349ece07af4518ec5bd"
DIMENSIONS = 768
MAX_LENGTH = 512
DEFAULT_MODELS = Path(__file__).resolve().parents[3] / "models"

MODEL_PROFILE = {
    "base": BASE_REVISION,
    "document_adapter": DOCUMENT_REVISION,
    "query_adapter": QUERY_REVISION,
    "pooling": "cls",
    "max_length": MAX_LENGTH,
    "normalize": False,
    "document_precision": "fp16",
    "query_precision": "fp32",
}
MODEL_SIGNATURE = hashlib.sha256(
    json.dumps(MODEL_PROFILE, sort_keys=True).encode()
).hexdigest()
QUERY_MODEL = f"specter2-adhoc-{MODEL_SIGNATURE[:16]}"


def model_paths(models: Path, *, query: bool) -> tuple[Path, Path]:
    base = models / "models--allenai--specter2_base" / "snapshots" / BASE_REVISION
    adapter = (
        models
        / (
            "models--allenai--specter2_adhoc_query"
            if query
            else "models--allenai--specter2"
        )
        / "snapshots"
        / (QUERY_REVISION if query else DOCUMENT_REVISION)
    )
    return base, adapter


def document_fingerprint(title: str, abstract: str) -> str:
    """输入变化只使论文向量失效；引用数和 TLDR 不属于模型输入。"""
    return hashlib.sha256(
        json.dumps([title, abstract], ensure_ascii=False).encode()
    ).hexdigest()
