import pytest

from common.utils.ranking.tokenizer import (
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
def test_tokenizers_tokenize_cjk(
    tokenizer_cls: type[JiebaRankingTokenizer] | type[ThuLacRankingTokenizer],
    expected_token: str,
) -> None:
    tokenizer = tokenizer_cls()
    tokens = tokenizer.tokenize("我爱北京天安门和人工智能")

    assert "北京" in tokens
    assert "天安门" in tokens
    assert expected_token in tokens


def test_tokenizer_always_normalizes_and_splits_compound_tokens() -> None:
    tokens = JiebaRankingTokenizer().tokenize("ＧＰＴ-4 API_KEY")

    assert tokens == ("gpt-4", "gpt", "4", "api_key", "api", "key")


def test_tokenizer_adds_string_cjk_bigrams() -> None:
    tokens = JiebaRankingTokenizer().tokenize("中文")

    assert "中文" in tokens
    assert all(isinstance(token, str) for token in tokens)
