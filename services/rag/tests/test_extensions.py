"""文档代码使用真实 DI/摄入接口执行；不启动应用或访问外部服务。"""

import ast
import re
from pathlib import Path

import yaml
from dependency_injector import providers

from rag.container import Container

DEMO = Path(__file__).resolve().parents[1] / "demo"


def examples():
    return {
        name: code
        for name, code in re.findall(
            r"```python\n# example: ([\w-]+)\n(.*?)```",
            (DEMO / "extensions.md").read_text(encoding="utf-8"),
            re.DOTALL,
        )
    }


def test_extension_composition_and_substitute_example():
    snippets = examples()
    namespace = {}
    exec(snippets["extension-composition"], namespace)  # noqa: S102 - 仓库内固定示例
    exec(snippets["extension-test"], namespace)  # noqa: S102
    # 直接执行注册示例，平台 publication 不为垂类替换或复制。
    publisher = namespace["platform"].publication()
    exec(snippets["pipeline-registration"], namespace)  # noqa: S102
    routes = namespace["platform"].pipeline_routes()
    assert routes["history"] is namespace["history"].pipeline()
    assert publisher._pipelines is routes


def test_extension_accepts_real_platform_factories(platform_settings, graph_ontology):
    namespace = {}
    exec(examples()["extension-composition"], namespace)  # noqa: S102
    platform = Container(settings=platform_settings)
    for name in (
        "content_storage",
        "publication_store",
        "embedding_client",
        "entity_vector_storage",
        "relationship_vector_storage",
        "vector_storage",
        "instruction_client",
    ):
        getattr(platform, name).override(providers.Object(object()))
    history = namespace["HistoryContainer"](platform=platform, ontology=graph_ontology)
    assert history.pipeline().transformers[0]._ontology is graph_ontology
    assert history.pipeline().indexers[:-1] == platform.pipeline().indexers
    assert history.pipeline().indexers[-1]._ontology is graph_ontology
    assert platform.pipeline().transformers == []


def test_all_extension_examples_compile_and_settings_sample_valid(platform_settings):
    for name, snippet in examples().items():
        compile(snippet, name, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    values = yaml.safe_load(
        (DEMO / "platform-settings.yaml").read_text(encoding="utf-8")
    )
    settings = type(platform_settings)(**values)
    assert not settings.KAFKA_ENABLED and not settings.ENABLE_GRAPH_STORAGE
