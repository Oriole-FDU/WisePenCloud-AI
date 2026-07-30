import importlib
import sys

from chat.application.utils.ranking.pipeline import RankingPipeline
from chat.application.utils.ranking.rerankers import ZeroEntropyReranker


def test_presets_are_fixed_global_pipelines(monkeypatch) -> None:
    settings = type(
        "Settings",
        (),
        {
            "ZERO_ENTROPY_API_KEY": "test-key",
            "RERANKER_MODEL": "test-model",
        },
    )()
    config_module = type("ConfigModule", (), {"settings": settings})()
    monkeypatch.setitem(sys.modules, "chat.core.config.app_settings", config_module)
    monkeypatch.delitem(
        sys.modules,
        "chat.application.utils.ranking.presets",
        raising=False,
    )

    presets = importlib.import_module("chat.application.utils.ranking.presets")

    assert isinstance(presets.READ_RANKED_EXPAND_PIPELINE, RankingPipeline)
    assert isinstance(presets.KNOWLEDGE_GRAPH_PATH_PIPELINE, RankingPipeline)
    assert isinstance(presets.KNOWLEDGE_SEARCH_PIPELINE, RankingPipeline)
    assert isinstance(presets.WEB_SEARCH_PIPELINE, RankingPipeline)
    assert isinstance(presets.READ_RANKED_EXPAND_PIPELINE.reranker, ZeroEntropyReranker)
    assert (
        presets.READ_RANKED_EXPAND_PIPELINE.reranker
        is presets.KNOWLEDGE_SEARCH_PIPELINE.reranker
    )
    assert (
        presets.KNOWLEDGE_GRAPH_PATH_PIPELINE.reranker
        is presets.KNOWLEDGE_SEARCH_PIPELINE.reranker
    )
