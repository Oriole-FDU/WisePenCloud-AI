import os
import subprocess
import sys

import pytest
from common.utils.markdown import MarkdownChunker, TiktokenTokenCounter


def test_cold_initialization_works_offline(tmp_path) -> None:
    # 独立进程确保测试覆盖首次构造，不依赖模块级 encoding 缓存状态。
    code = """
from unittest.mock import patch

import tiktoken

with patch.object(tiktoken, "get_encoding", side_effect=AssertionError("download")):
    from common.utils.markdown import TiktokenTokenCounter

    assert TiktokenTokenCounter().count("中文 emoji 🙂") == 3
"""
    subprocess.run(
        [sys.executable, "-c", code],
        env={**os.environ, "TIKTOKEN_CACHE_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    "text",
    [
        "English words " * 80,
        "中文文本" * 60,
        "混合 English 中文🙂\r\n\u2028" * 50,
        "<|endoftext|> literal",
    ],
)
def test_split_preserves_unicode_and_all_characters(text) -> None:
    counter = TiktokenTokenCounter()
    parts = counter.split(text, 32)
    assert "".join(parts) == text
    assert all(counter.count(part) <= 32 for part in parts)
    assert all("\ufffd" not in part for part in parts)


def test_count_uses_token_units() -> None:
    counter = TiktokenTokenCounter()
    english = "a" * 20
    chinese = "中" * 20
    assert len(english) == len(chinese)
    assert counter.count(english) != counter.count(chinese)
    assert counter.count(chinese) == 20


@pytest.mark.parametrize("soft,hard", [(0, 1), (-1, 2), (3, 2)])
def test_invalid_budgets(soft, hard) -> None:
    with pytest.raises(ValueError):
        MarkdownChunker(soft, hard)
