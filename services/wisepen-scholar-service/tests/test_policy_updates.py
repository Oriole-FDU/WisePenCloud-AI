"""通过配置监听和公开用例验证优先级更新、请求快照与失败保留。"""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
import yaml
from v2.nacos import NacosConfigService

from common.cloud.nacos_client import NacosClientManager
from common.core.exceptions import ServiceException
from pdf_fakes import make_pdf_service
from policy_fakes import make_policy_store
from scholar.application.discovery import PaperDiscoveryQuery, PaperDiscoveryService
from scholar.application.lookup import PaperLookup, PaperLookupService
from scholar.application.lookup_runner import LookupBinding, LookupRunner
from scholar.application.search import PaperSearch, PaperSearchService, SearchBinding, SourceScope
from scholar.core.config.policy_settings import PolicySettings, watch_policies
from scholar.core.providers import ProviderPolicy
from scholar.domain.interfaces.paper_discoverer import PaperDiscovery
from scholar.domain.interfaces.paper_lookup import PaperLookupMatch
from scholar.domain.interfaces.paper_searcher import PaperSearchBatch
from scholar.domain.models.fields import AcademicField, FieldPolicy
from scholar.domain.models.identifiers import IdentifierKind
from scholar.domain.models.paper import Paper


CAPABILITIES = {
    "search_sources": {"arxiv", "openalex", "huggingface"},
    "lookup_sources": {"arxiv", "s2", "openalex", "huggingface"},
    "discovery_sources": {"huggingface"},
}


class MemoryCache:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def put(self, key, value):
        self.values[key] = value


class Upstream:
    def __init__(self, source, arxiv_id="2401.00001"):
        self.source = source
        self.search_calls = 0
        self.lookup_calls = 0
        self.paper = Paper(
            arxiv_id=arxiv_id, doi=None, title=f"{source} title", abstract="Abstract",
            authors=["Author"], publication_date=None, year=2024,
            url="https://example.test/paper", pdf_url="https://example.test/paper.pdf",
            source=source, metadata={source: {"available": True}},
        )

    async def search(self, **kwargs):
        self.search_calls += 1
        return PaperSearchBatch([self.paper])

    async def lookup(self, ids, *, api_key=None):
        self.lookup_calls += 1
        return [PaperLookupMatch([self.paper]) for _ in ids]

    async def discover(self, **kwargs):
        return PaperDiscovery([self.paper], None, None)


def lookup_binding(provider):
    return LookupBinding(provider, None, ProviderPolicy(
        identifier_kinds=[IdentifierKind.ARXIV],
        project_metadata=lambda paper: paper.metadata[provider.source],
    ))


@pytest.fixture
def nacos(monkeypatch):
    client = AsyncMock(spec=NacosConfigService)
    monkeypatch.setattr(
        NacosConfigService, "create_config_service", AsyncMock(return_value=client)
    )
    manager = NacosClientManager(SimpleNamespace(
        NACOS_SERVER_ADDR="127.0.0.1:8848", NACOS_NAMESPACE_ID="",
        NACOS_USERNAME=None, NACOS_PASSWORD=None,
        NACOS_DATA_ID="scholar-test.yaml", NACOS_GROUP="DEFAULT_GROUP",
    ))
    store = make_policy_store({
        AcademicField.COMPUTER_SCIENCE: FieldPolicy(["arxiv", "huggingface"])
    })
    payload = PolicySettings(SCHOLAR_POLICIES=store.current).model_dump(mode="json")
    client.get_config.return_value = yaml.safe_dump(payload)
    return manager, client, store, payload


@pytest.mark.asyncio
async def test_source_priority_updates_on_same_service_and_reuses_source_caches(nacos):
    manager, client, store, payload = nacos
    arxiv = Upstream("arxiv")
    hf = Upstream("huggingface", "2401.00002")
    service = PaperSearchService(
        {"arxiv": SearchBinding(arxiv), "huggingface": SearchBinding(hf, requires_byok=True)},
        store, MemoryCache(), LookupRunner({}), make_pdf_service(),
    )
    query = PaperSearch("priority", field=AcademicField.COMPUTER_SCIENCE)
    credentials = {"huggingface": "user-hf"}
    async with watch_policies(store, manager, **CAPABILITIES):
        await service.search(
            PaperSearch("priority", field=query.field, source_scope=SourceScope.MULTI_SOURCE),
            credentials=credentials,
        )
        before = await service.search(query, credentials=credentials)
        assert before.papers[0].source == "arxiv"
        listener = client.add_listener.call_args.kwargs["listener"]
        payload["SCHOLAR_POLICIES"]["fields"]["computer_science"]["searchers"] = ["huggingface", "arxiv"]
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", yaml.safe_dump(payload))
        after = await service.search(query, credentials=credentials)
        assert after.papers[0].source == "huggingface"
        assert after.execution[0].cache_hit
        assert (arxiv.search_calls, hf.search_calls) == (1, 1)
        without_key = await service.search(query)
        assert without_key.papers[0].source == "arxiv"
    client.remove_listener.assert_awaited_once_with(
        data_id="scholar-test.yaml", group="DEFAULT_GROUP", listener=listener
    )
    client.shutdown.assert_awaited_once()


@pytest.mark.asyncio
async def test_inflight_search_uses_original_common_and_field_enrichment(nacos):
    manager, client, store, payload = nacos
    entered, release = asyncio.Event(), asyncio.Event()

    class BlockingSearch(Upstream):
        async def search(self, **kwargs):
            entered.set()
            await release.wait()
            return await super().search(**kwargs)

    payload["SCHOLAR_POLICIES"]["common_enrichers"] = ["arxiv"]
    payload["SCHOLAR_POLICIES"]["fields"]["computer_science"]["searchers"] = ["huggingface"]
    client.get_config.return_value = yaml.safe_dump(payload)
    arxiv, s2, openalex = Upstream("arxiv"), Upstream("s2"), Upstream("openalex")
    service = PaperSearchService(
        {"huggingface": SearchBinding(BlockingSearch("huggingface"))}, store, MemoryCache(),
        LookupRunner({
            "arxiv": lookup_binding(arxiv), "s2": lookup_binding(s2),
            "openalex": lookup_binding(openalex),
        }),
        make_pdf_service(),
    )
    async with watch_policies(store, manager, **CAPABILITIES):
        task = asyncio.create_task(service.search(PaperSearch("inflight", field=AcademicField.COMPUTER_SCIENCE)))
        await entered.wait()
        payload["SCHOLAR_POLICIES"]["common_enrichers"] = ["s2"]
        payload["SCHOLAR_POLICIES"]["fields"]["computer_science"]["field_enrichers"] = ["openalex"]
        listener = client.add_listener.call_args.kwargs["listener"]
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", yaml.safe_dump(payload))
        release.set()
        result = await task
        assert result.papers
        assert (arxiv.lookup_calls, s2.lookup_calls, openalex.lookup_calls) == (1, 0, 0)
        await service.search(PaperSearch("after update", field=AcademicField.COMPUTER_SCIENCE))
        assert (arxiv.lookup_calls, s2.lookup_calls, openalex.lookup_calls) == (1, 1, 1)


@pytest.mark.asyncio
async def test_lookup_order_and_discoverer_follow_current_snapshot(nacos):
    manager, client, store, payload = nacos
    arxiv, s2 = Upstream("arxiv"), Upstream("s2")
    lookup = PaperLookupService(
        LookupRunner({"arxiv": lookup_binding(arxiv), "s2": lookup_binding(s2)}),
        make_pdf_service(), policies=store,
    )
    discover = PaperDiscoveryService(
        {"huggingface": Upstream("huggingface")}, store, MemoryCache(), make_pdf_service()
    )
    query = PaperDiscoveryQuery(AcademicField.MEDICINE)
    async with watch_policies(store, manager, **CAPABILITIES):
        before = await lookup.lookup(PaperLookup(["2401.00001"]))
        assert before.items[0].paper.source == "arxiv"
        with pytest.raises(ServiceException):
            await discover.discover(query, credentials={"huggingface": "user-hf"})
        listener = client.add_listener.call_args.kwargs["listener"]
        payload["SCHOLAR_POLICIES"]["common_enrichers"] = ["s2", "arxiv"]
        payload["SCHOLAR_POLICIES"]["fields"]["medicine"]["discoverer"] = "huggingface"
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", yaml.safe_dump(payload))
        after = await lookup.lookup(PaperLookup(["2401.00001"]))
        assert after.items[0].paper.source == "s2"
        assert (await discover.discover(query, credentials={"huggingface": "user-hf"})).papers
        payload["SCHOLAR_POLICIES"]["fields"]["medicine"]["discoverer"] = None
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", yaml.safe_dump(payload))
        with pytest.raises(ServiceException):
            await discover.discover(query, credentials={"huggingface": "user-hf"})


@pytest.mark.asyncio
async def test_invalid_updates_preserve_valid_policy_and_later_update_recovers(nacos, monkeypatch):
    manager, client, store, payload = nacos
    errors = []
    monkeypatch.setattr("scholar.core.config.policy_settings.error", lambda message, **details: errors.append(details))
    async with watch_policies(store, manager, **CAPABILITIES):
        listener = client.add_listener.call_args.kwargs["listener"]
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", 'FROM_SOURCE_SECRET: "hidden-secret"\nSCHOLAR_POLICIES: [')
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", 'FROM_SOURCE_SECRET: "hidden-secret"')
        payload["SCHOLAR_POLICIES"]["fields"]["computer_science"]["searchers"] = ["unknown"]
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", yaml.safe_dump(payload))
        assert store.current.fields[AcademicField.COMPUTER_SCIENCE].searchers == ["arxiv", "huggingface"]
        assert len(errors) == 3
        assert "hidden-secret" not in str(errors)
        payload["SCHOLAR_POLICIES"]["fields"]["computer_science"]["searchers"] = ["huggingface", "arxiv"]
        await listener("", "DEFAULT_GROUP", "scholar-test.yaml", yaml.safe_dump(payload))
        assert store.current.fields[AcademicField.COMPUTER_SCIENCE].searchers == ["huggingface", "arxiv"]


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["invalid_initial", "subscription"])
async def test_initial_configuration_and_subscription_errors_propagate_and_close_client(nacos, failure):
    manager, client, store, payload = nacos
    if failure == "invalid_initial":
        del payload["SCHOLAR_POLICIES"]["fields"]["medicine"]
        client.get_config.return_value = yaml.safe_dump(payload)
        exception = ValueError
    else:
        client.add_listener.side_effect = RuntimeError("subscription failed")
        exception = RuntimeError
    with pytest.raises(exception):
        async with watch_policies(store, manager, **CAPABILITIES):
            pytest.fail("invalid startup must not serve requests")
    client.shutdown.assert_awaited_once()


def test_local_yaml_or_json_and_nacos_mapping_produce_same_typed_policy(nacos):
    _, _, store, payload = nacos
    as_mapping = PolicySettings.model_validate(payload)
    as_yaml = PolicySettings(SCHOLAR_POLICIES=yaml.safe_dump(payload["SCHOLAR_POLICIES"]))
    as_json = PolicySettings(SCHOLAR_POLICIES=json.dumps(payload["SCHOLAR_POLICIES"]))
    assert as_mapping.SCHOLAR_POLICIES == as_yaml.SCHOLAR_POLICIES == as_json.SCHOLAR_POLICIES == store.current
