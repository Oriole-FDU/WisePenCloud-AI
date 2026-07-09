from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("jieba")
pytest.importorskip("thulac")

ranking_engine_package = types.ModuleType("chat.application.utils.ranking_engine")
ranking_engine_package.__path__ = [
    str(
        Path(__file__).resolve().parents[2]
        / "src"
        / "chat"
        / "application"
        / "utils"
        / "ranking_engine"
    )
]
sys.modules["chat.application.utils.ranking_engine"] = ranking_engine_package

from chat.application.utils.ranking_engine.tokenizer.ranking_tokenizers import (
    JiebaRankingTokenizer,
    ThuLacRankingTokenizer,
)


@pytest.mark.parametrize(
    ("tokenizer_cls", "expected_token"),
    [
        (JiebaRankingTokenizer, "人工智能"),
        (ThuLacRankingTokenizer, "人工智能"),
    ],
)
def test_ranking_tokenizers_tokenize_cjk(
    tokenizer_cls: type[JiebaRankingTokenizer] | type[ThuLacRankingTokenizer],
    expected_token: str,
) -> None:
    tokenizer = tokenizer_cls()
    tokens = tokenizer.tokenize("我爱北京天安门和人工智能")

    assert tokens
    assert "北京" in tokens
    assert "天安门" in tokens
    assert expected_token in tokens
