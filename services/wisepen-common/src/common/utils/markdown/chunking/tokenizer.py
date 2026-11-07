"""Common 的离线 token 预算接口，与 embedding provider 解耦。"""

from __future__ import annotations

import base64
from functools import lru_cache
from importlib.resources import files
from typing import Protocol

import tiktoken


class TokenCounter(Protocol):
    """结构装箱依赖 count；不可再按词句切分时使用 split。"""

    name: str

    def count(self, text: str) -> int:
        """返回文本的 token 数量。"""
        ...

    def split(self, text: str, max_tokens: int) -> list[str]:
        """将文本按 token 上限切分为多个片段。"""
        ...


@lru_cache(maxsize=1)
def _local_encoding() -> tiktoken.Encoding:
    """加载随包分发的 o200k_base BPE 表，构建离线 Encoding。

    避免 get_encoding() 在冷启动或离线环境下尝试下载或访问用户缓存。
    """
    # 读取打包的 BPE 表文件，解析为 {token_bytes: rank} 映射。
    data = (
        files("common.utils.markdown")
        .joinpath("chunking/data/o200k_base.tiktoken")
        .read_bytes()
    )
    ranks = {
        base64.b64decode(token): int(rank)
        for token, rank in (line.split() for line in data.splitlines())
    }

    # 直接构造 Encoding，确保完全离线可用。
    return tiktoken.Encoding(
        name="o200k_base",
        pat_str=(
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]*"
            r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]+(?i:'s|'t|'re|'ve|'m|'ll|'d)?|"
            r"[^\r\n\p{L}\p{N}]?[\p{Lu}\p{Lt}\p{Lm}\p{Lo}\p{M}]+"
            r"[\p{Ll}\p{Lm}\p{Lo}\p{M}]*(?i:'s|'t|'re|'ve|'m|'ll|'d)?|"
            r"\p{N}{1,3}| ?[^\s\p{L}\p{N}]+[\r\n/]*|\s*[\r\n]+|"
            r"\s+(?!\S)|\s+"
        ),
        mergeable_ranks=ranks,
        special_tokens={
            "<|endoftext|>": 199999,
            "<|endofprompt|>": 200018,
        },
    )


class TiktokenTokenCounter:
    """使用固定 o200k_base 表计数，缓存属于调用实例。"""

    def __init__(self, encoding_name: str = "o200k_base") -> None:
        """初始化计数器；仅支持内置的 o200k_base 编码。"""
        if encoding_name != "o200k_base":
            raise ValueError("only the bundled o200k_base encoding is supported")

        self.name = f"tiktoken:{encoding_name}"
        self._encoding = _local_encoding()
        # 实例级缓存，避免重复编码相同文本；限制大小防止内存膨胀。
        self._counts: dict[str, int] = {}

    def _count_cached(self, text: str) -> int:
        """带缓存的 token 计数；缓存满时移除最早插入的条目。"""
        cached = self._counts.get(text)
        if cached is not None:
            return cached

        # 文档中的 special-token 拼写是普通正文，不是 tokenizer 控制符。
        value = len(self._encoding.encode(text, disallowed_special=()))

        if len(self._counts) >= 4096:
            # 简单 FIFO 淘汰，避免无限增长。
            self._counts.pop(next(iter(self._counts)))
        self._counts[text] = value
        return value

    def count(self, text: str) -> int:
        """返回文本的 token 数量（带缓存）。"""
        return self._count_cached(text)

    def split(self, text: str, max_tokens: int) -> list[str]:
        """按 Unicode 字符边界拆分文本，不通过 token decode 破坏汉字/emoji。

        采用二分逼近的方式，保证每个片段不超过 max_tokens。
        """
        if max_tokens <= 0:
            raise ValueError("max_tokens must be positive")

        parts: list[str] = []
        start = 0

        while start < len(text):
            # 初始猜测：按每 token 最多 4 个字符估算结束位置。
            end = min(len(text), start + max_tokens * 4)

            # 若超出 token 限制，则二分缩小 end，直到满足要求。
            while self.count(text[start:end]) > max_tokens:
                if end == start + 1:
                    raise ValueError("a Unicode character exceeds max_tokens")
                end = start + max(1, (end - start) // 2)

            parts.append(text[start:end])
            start = end

        return parts


def default_token_counter() -> TokenCounter:
    """创建 Common 默认的 token 计数器，不依赖网络。"""
    return TiktokenTokenCounter()
