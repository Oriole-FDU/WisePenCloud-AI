"""扩展注册、集合声明和网关配置的公开行为。"""

from types import SimpleNamespace

import pytest
from dependency_injector import providers

from rag.container import Container
from rag.extensions import register_extensions
from rag.storage.vector import IndexableField, IndexKind, VectorCollection


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


@pytest.mark.parametrize("secret", [None, " ", "gateway"])
def test_employment_manifest_declares_collection_and_requires_gateway(
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
    assert binding.storage.collection == "employment_contract_vectors_v4"
    assert manifest.api is not None
    assert IndexableField("year", IndexKind.INTEGER) in binding.metadata_index_fields
    assert (
        IndexableField("employee", IndexKind.KEYWORD) in binding.metadata_index_fields
    )
    assert {field.name for field in binding.metadata_index_fields} == {
        "resource_id",
        "year",
        "employee",
    }
