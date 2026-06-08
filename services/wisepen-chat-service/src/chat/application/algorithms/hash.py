import hashlib
import json


def stable_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]

def stable_hash_json(value: object) -> str:
    """计算对象的稳定哈希值。

    使用 JSON 序列化后计算 SHA256 哈希，确保相同内容产生相同的哈希值。
    """
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
