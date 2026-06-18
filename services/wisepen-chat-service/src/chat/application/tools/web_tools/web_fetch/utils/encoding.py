from __future__ import annotations

from charset_normalizer import from_bytes as detect_encoding


def decode_bytes(raw: bytes, declared_encoding: str | None) -> str:
    """按声明编码 → charset-normalizer → UTF-8 优先级解码。"""
    if declared_encoding:
        try:
            return raw.decode(declared_encoding, errors="replace")
        except LookupError:
            pass

    result = detect_encoding(
        raw,
        cp_isolation=["utf-8", "gbk", "big5", "shift_jis", "euc_kr"],
    ).best()

    return str(result) if result is not None else raw.decode("utf-8", errors="replace")
