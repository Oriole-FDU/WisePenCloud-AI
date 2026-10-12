"""文档代码使用真实 DI/摄入接口执行；不启动应用或访问外部服务。"""

import ast
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from dependency_injector import providers

from rag.container import Container
from rag.extensions import register_extensions
from rag.storage.vector import IndexableField, IndexKind, VectorCollection

DEMO = Path(__file__).resolve().parents[1] / "demo"
GUIDE = Path(__file__).resolve().parents[1] / "src/rag/extensions/extensions.md"


def examples():
    return {
        name: code
        for name, code in re.findall(
            r"```python\n# example: ([\w-]+)\n(.*?)```",
            GUIDE.read_text(encoding="utf-8"),
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


def test_typed_index_example_and_local_guide_links():
    namespace = {}
    exec(examples()["dedicated-indexes"], namespace)  # noqa: S102 - 仓库固定示例
    binding = namespace["contract_collection"](object(), 2)
    assert binding.storage.collection == "employment_contract_vectors_v4"
    assert IndexableField("year", IndexKind.INTEGER) in binding.metadata_index_fields
    for guide in (GUIDE, GUIDE.parent / "employment/README.md"):
        for target in re.findall(r"\]\(([^)]+)\)", guide.read_text(encoding="utf-8")):
            if "://" not in target and not target.startswith("#"):
                assert (guide.parent / target.split("#")[0]).is_file(), target


def test_register_manifest_and_reject_collection_collision(
    platform_settings, monkeypatch
):
    import rag.extensions as registry
    from rag.extensions.base import Extension

    settings = type(platform_settings)(
        **(platform_settings.model_dump() | {"EXTENSIONS": ["sample"]})
    )
    platform = Container(settings=settings)
    platform.vector_storages.override(
        providers.Object([SimpleNamespace(collection="base")])
    )
    platform.vector_collections.override(providers.Object([]))
    extra = SimpleNamespace(collection="dedicated")
    binding = VectorCollection(extra, [IndexableField("year", IndexKind.INTEGER)])
    manifest = Extension(pipeline=object(), vector_collections=[binding])
    monkeypatch.setitem(
        registry._BUILDERS,
        "sample",
        lambda **kwargs: SimpleNamespace(extension=lambda: manifest),
    )
    assert register_extensions(platform) == {"sample": manifest}
    assert platform.pipeline_routes()["sample"] is manifest.pipeline
    assert platform.vector_storages()[-1] is extra
    with pytest.raises(ValueError, match="collection already managed"):
        register_extensions(platform)


def test_register_extensions_rejects_unknown_name(platform_settings):
    settings = type(platform_settings)(
        **{**platform_settings.model_dump(), "EXTENSIONS": ["ghost"]}
    )
    platform = Container(settings=settings)
    with pytest.raises(ValueError, match="ghost"):
        register_extensions(platform)


async def test_employment_readme_load_and_projection_rebuild_example(
    platform_settings, monkeypatch
):
    import json
    from unittest.mock import AsyncMock

    from rag.application.publication import DocumentReadyEvent

    readme = GUIDE.parent / "employment/README.md"
    snippets = {
        name: code
        for name, code in re.findall(
            r"```python\n# example: ([\w-]+)\n(.*?)```",
            readme.read_text(encoding="utf-8"),
            re.DOTALL,
        )
    }
    namespace = {}
    for snippet in snippets.values():
        exec(snippet, namespace)  # noqa: S102 - 仓库固定示例
    publication = SimpleNamespace(publish=AsyncMock())
    platform = SimpleNamespace(publication=lambda: publication)
    await namespace["load_contracts"](platform, readme.with_name("contracts.jsonl"))
    assert publication.publish.await_count == 4
    document = DocumentReadyEvent.model_validate(
        json.loads(readme.with_name("document-ready.json").read_text(encoding="utf-8"))
    ).to_document()
    from rag.core.schema import Chunk, ContentRevision

    rev = ContentRevision.create(
        resource_id=document.resource_id,
        document_version=document.document_version,
        text=document.text,
    )
    state = SimpleNamespace(destroyed=False, applied_revision=rev)
    chunk = Chunk(
        "chunk",
        document.resource_id,
        document.content_revision,
        0,
        None,
        [],
        "正文",
        2,
        [],
        metadata=document.metadata,
    )
    store = SimpleNamespace(get_states=AsyncMock(return_value=[state]))
    content = SimpleNamespace(get_revision_chunks=AsyncMock(return_value=[chunk]))
    indexer = SimpleNamespace(index=AsyncMock())
    platform = SimpleNamespace(
        publication_store=lambda: store, content_storage=lambda: content
    )
    employment = SimpleNamespace(contract_indexer=lambda: indexer)
    assert await namespace["rebuild_contract_vectors"](
        platform, employment, document.resource_id
    )
    indexer.index.assert_awaited_once_with([chunk])
    store.get_states.side_effect = [[state], []]
    indexer.index.reset_mock()
    assert not await namespace["rebuild_contract_vectors"](
        platform, employment, document.resource_id
    )
    indexer.index.assert_not_awaited()


@pytest.mark.parametrize("secret", [None, " ", "gateway"])
def test_real_employment_manifest_uses_shared_clients_and_requires_gateway(
    platform_settings, secret
):
    from rag.extensions.employment import EmploymentContainer

    settings = type(platform_settings)(
        **(platform_settings.model_dump() | {"FROM_SOURCE_SECRET": secret})
    )
    platform = Container(settings=settings)
    for name in (
        "qdrant_client",
        "content_storage",
        "publication_store",
        "acl_repository",
        "embedding_client",
        "instruction_client",
        "graph_storage",
        "rerank_client",
    ):
        getattr(platform, name).override(providers.Object(object()))
    platform.rerank_client.override(
        providers.Object(SimpleNamespace(model="test", rerank=None))
    )
    employment = EmploymentContainer(platform=platform)
    if secret is None or not secret.strip():
        with pytest.raises(ValueError, match="FROM_SOURCE_SECRET"):
            employment.extension()
        return
    manifest = employment.extension()
    binding = manifest.vector_collections[0]
    storage = binding.storage
    assert storage._client is platform.qdrant_client()
    assert storage._dense_vector_size == settings.EMBEDDING_DIMENSIONS
    assert storage._sparse_vector_name is None
    assert IndexableField("year", IndexKind.INTEGER) in binding.metadata_index_fields
    assert (
        IndexableField("employee", IndexKind.KEYWORD) in binding.metadata_index_fields
    )
    assert {field.name for field in binding.metadata_index_fields} == {
        "resource_id",
        "year",
        "employee",
    }
    assert employment.contracts()._vector_reader is storage
    assert employment.contracts()._embedding_client is platform.embedding_client()
    assert manifest.pipeline.indexers[-1] is employment.contract_indexer()
    assert employment.tools()._graph_retrieval is platform.graph_retrieval()
