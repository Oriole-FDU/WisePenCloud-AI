"""图抽取的来源、增量、补漏、预算和异常收尾合同。"""

import asyncio
import copy
import json
from dataclasses import asdict, replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import AsyncOpenAI
from pydantic import ValidationError

from common.utils.ai_clients import InstructionClient
from rag.domain.models import GraphNode
from rag.ingestion import IngestionPipeline
from rag.ingestion.indexers import ChunkFactsIndexer
from rag.ingestion.transformers import GraphExtractor
from rag.ingestion.transformers.graph import GraphExtraction


def extraction(*, entities=None, relationships=None):
    return GraphExtraction.model_validate(
        {
            "entities": entities
            if entities is not None
            else [
                {"name": "张三", "category": "Person", "description": "原描述"},
                {"name": "甲公司", "category": "Company"},
            ],
            "relationships": relationships
            if relationships is not None
            else [
                {
                    "source": {"name": "张三", "category": "Person"},
                    "target": {"name": "甲公司", "category": "Company"},
                    "relation_type": "works_at",
                    "description": "原关系",
                }
            ],
        }
    )


@pytest.fixture
def client():
    return SimpleNamespace(
        model="graph-model", structured_complete=AsyncMock(return_value=extraction())
    )


async def test_sources_material_metadata_and_bson_round_trip(
        graph_ontology, client, chunk, storage
):
    chunk.section_path = ["工作档案"]
    chunk.raw_text = "张三就职于甲公司"
    chunk.metadata.update(contextual_context="不应抽取的背景", custom="保留")
    before = asdict(chunk)
    extractor = GraphExtractor(ontology=graph_ontology, client=client)
    chunks = [chunk]
    assert await extractor.transform(chunks) is chunks
    assert {
               key: value for key, value in asdict(chunk).items() if key != "metadata"
           } == {key: value for key, value in before.items() if key != "metadata"}
    assert chunk.metadata["custom"] == "保留"
    assert chunk.metadata["contextual_context"] == "不应抽取的背景"
    messages = client.structured_complete.await_args.kwargs["messages"]
    assert json.loads(messages[1]["content"]) == {
        "section_path": ["工作档案"],
        "raw_text": chunk.raw_text,
    }
    assert "不应抽取的背景" not in json.dumps(messages, ensure_ascii=False)
    assert (
            client.structured_complete.await_args.kwargs["response_model"]
            is GraphExtraction
    )
    assert client.structured_complete.await_args.kwargs["max_tokens"] == 4096
    assert chunk.metadata["entities"][0]["source_id"] == chunk.chunk_id
    edge = chunk.metadata["relationships"][0]
    assert (
            edge["source_id"] == "work:Person:张三"
            and edge["target_id"] == "work:Company:甲公司"
    )
    assert edge["source_ids"] == [chunk.chunk_id]
    await ChunkFactsIndexer(storage).index(chunks)
    restored = (await storage.get_chunks([chunk.chunk_id]))[0]
    assert restored == chunk
    assert isinstance(restored.metadata["entities"][0], dict)


async def test_invalid_facts_are_dropped_and_first_valid_id_wins(
        graph_ontology, client, chunk
):
    client.structured_complete.return_value = extraction(
        entities=[
            {"name": " ALICE  SMITH ", "category": "Person", "description": "first"},
            {"name": "alice smith", "category": "Person", "description": "duplicate"},
            {"name": "alice smith", "category": "Company"},
            {"name": "未知", "category": "Unknown"},
            {"name": " ", "category": "Person"},
        ],
        relationships=[
            {
                "source": {"name": "alice smith", "category": "Person"},
                "target": {"name": "alice smith", "category": "Company"},
                "relation_type": "works_at",
                "description": "first",
            },
            {
                "source": {"name": "alice smith", "category": "Person"},
                "target": {"name": "alice smith", "category": "Company"},
                "relation_type": "works_at",
                "description": "duplicate",
            },
            {
                "source": {"name": "alice smith", "category": "Company"},
                "target": {"name": "alice smith", "category": "Person"},
                "relation_type": "works_at",
            },
            {
                "source": {"name": "missing", "category": "Person"},
                "target": {"name": "alice smith", "category": "Company"},
                "relation_type": "works_at",
            },
            {
                "source": {"name": "alice smith", "category": "Person"},
                "target": {"name": "alice smith", "category": "Company"},
                "relation_type": "unknown",
            },
        ],
    )
    await GraphExtractor(ontology=graph_ontology, client=client).transform([chunk])
    nodes = [GraphNode(**value) for value in chunk.metadata["entities"]]
    assert [node.id for node in nodes] == [
        "work:Person:alice smith",
        "work:Company:alice smith",
    ]
    assert nodes[0].description == "first"
    assert len(chunk.metadata["relationships"]) == 1
    assert chunk.metadata["relationships"][0]["description"] == "first"


async def test_gleaning_adds_only_and_can_reference_initial_entities(
        graph_ontology, client, chunk
):
    client.structured_complete.side_effect = [
        extraction(),
        extraction(
            entities=[
                {"name": "张三", "category": "Person", "description": "不得替换"},
                {"name": "乙公司", "category": "Company"},
            ],
            relationships=[
                {
                    "source": {"name": "张三", "category": "Person"},
                    "target": {"name": "甲公司", "category": "Company"},
                    "relation_type": "works_at",
                    "description": "不得替换",
                },
                {
                    "source": {"name": "张三", "category": "Person"},
                    "target": {"name": "乙公司", "category": "Company"},
                    "relation_type": "works_at",
                },
            ],
        ),
    ]
    await GraphExtractor(
        ontology=graph_ontology, client=client, gleaning=True
    ).transform([chunk])
    assert client.structured_complete.await_count == 2
    assert chunk.metadata["entities"][0]["description"] == "原描述"
    assert chunk.metadata["relationships"][0]["description"] == "原关系"
    assert len(chunk.metadata["relationships"]) == 2
    messages = client.structured_complete.await_args.kwargs["messages"]
    assert [message["role"] for message in messages] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert "不得修改或重复输出" in messages[-1]["content"]
    assert "没有增量" in messages[-1]["content"]
    assert "空列表" in messages[-1]["content"]
    assert "source_id" not in messages[-1]["content"]


async def test_empty_gleaning_preserves_initial_result(graph_ontology, client, chunk):
    client.structured_complete.side_effect = [
        extraction(),
        extraction(entities=[], relationships=[]),
    ]
    await GraphExtractor(
        ontology=graph_ontology, client=client, gleaning=True
    ).transform([chunk])
    assert (
            len(chunk.metadata["entities"]) == 2
            and len(chunk.metadata["relationships"]) == 1
    )


async def test_empty_result_is_success_and_incrementally_reused(
        graph_ontology, client, chunk
):
    client.structured_complete.return_value = extraction(entities=[], relationships=[])
    extractor = GraphExtractor(ontology=graph_ontology, client=client)
    await extractor.transform([chunk])
    fingerprint = chunk.metadata["graph_version"]
    await extractor.transform([chunk])
    assert client.structured_complete.await_count == 1
    assert chunk.metadata["entities"] == [] and chunk.metadata["relationships"] == []
    assert len(fingerprint) == 64


@pytest.mark.parametrize("change", ["model", "ontology", "text"])
async def test_changed_extraction_inputs_recompute_cached_facts(
        graph_ontology, client, chunk, change
):
    await GraphExtractor(ontology=graph_ontology, client=client).transform([chunk])
    # 模型、本体和正文改变都会影响抽取结果，旧缓存不能继续复用。
    if change == "model":
        client.model = "other-model"
    elif change == "ontology":
        graph_ontology = replace(graph_ontology, description="不同本体说明")
    else:
        chunk.raw_text += "新增正文"
    await GraphExtractor(ontology=graph_ontology, client=client).transform([chunk])
    assert client.structured_complete.await_count == 2


async def test_nonsemantic_changes_reuse_and_force_regenerates(
        graph_ontology, client, chunk
):
    await GraphExtractor(ontology=graph_ontology, client=client).transform([chunk])
    chunk.metadata.update(contextual_context="改变的背景", unrelated={"data": True})
    await GraphExtractor(
        ontology=graph_ontology, client=client, max_concurrency=1
    ).transform([chunk])
    assert client.structured_complete.await_count == 1
    await GraphExtractor(
        ontology=graph_ontology, client=client, reuse_existing=False
    ).transform([chunk])
    assert client.structured_complete.await_count == 2


async def test_structurally_invalid_cached_facts_are_regenerated(
        graph_ontology, client, chunk
):
    extractor = GraphExtractor(ontology=graph_ontology, client=client)
    await extractor.transform([chunk])
    chunk.metadata["entities"] = [{}]
    await extractor.transform([chunk])
    assert client.structured_complete.await_count == 2
    assert (
            len(chunk.metadata["entities"]) == 2
            and len(chunk.metadata["relationships"]) == 1
    )


class BudgetCounter:
    name = "controlled-budget"

    def __init__(self, *, schema_cost=1, glean_cost=1):
        self.schema_cost = schema_cost
        self.glean_cost = glean_cost

    def count(self, text):
        if '"$defs"' in text:
            return self.schema_cost
        if "检查同一块正文" in text:
            return self.glean_cost
        return 1


async def test_schema_counts_toward_first_budget_no_truncation(
        graph_ontology, client, chunk
):
    before = copy.deepcopy(chunk)
    with pytest.raises(ValueError, match="budget"):
        await GraphExtractor(
            ontology=graph_ontology,
            client=client,
            token_counter=BudgetCounter(schema_cost=10),
            max_input_tokens=11,
        ).transform([chunk])
    client.structured_complete.assert_not_awaited()
    assert chunk == before


async def test_over_budget_gleaning_keeps_first_facts(graph_ontology, client, chunk):
    await GraphExtractor(
        ontology=graph_ontology,
        client=client,
        token_counter=BudgetCounter(glean_cost=20),
        max_input_tokens=10,
        gleaning=True,
    ).transform([chunk])
    assert client.structured_complete.await_count == 1
    assert len(chunk.metadata["entities"]) == 2


async def test_failure_cancels_and_waits_other_tasks_without_metadata_changes(
        graph_ontology, client, chunk
):
    started = asyncio.Event()
    cleaned = asyncio.Event()
    first = replace(chunk, chunk_id="first", raw_text="slow", metadata={"old": True})
    second = replace(chunk, chunk_id="second", raw_text="fail", metadata={"old": True})
    chunks = [first, second]
    before = copy.deepcopy(chunks)

    async def generate(**kwargs):
        if json.loads(kwargs["messages"][1]["content"])["raw_text"] == "slow":
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned.set()
        await started.wait()
        raise RuntimeError("provider failure")

    client.structured_complete.side_effect = generate
    writer = SimpleNamespace(upsert_chunks=AsyncMock())
    pipeline = IngestionPipeline(
        chunker=SimpleNamespace(split=AsyncMock(return_value=chunks)),
        transformers=[GraphExtractor(ontology=graph_ontology, client=client)],
        indexers=[ChunkFactsIndexer(writer)],
    )
    with pytest.raises(RuntimeError, match="provider failure"):
        await pipeline.run(None)
    assert cleaned.is_set()
    assert chunks == before
    writer.upsert_chunks.assert_not_awaited()


async def test_gleaning_exception_propagates_without_replacing_old_metadata(
        graph_ontology, client, chunk
):
    chunk.metadata.update(entities=[{"old": True}], graph_version="old")
    before = copy.deepcopy(chunk)
    client.structured_complete.side_effect = [
        extraction(),
        RuntimeError("gleaning failure"),
    ]
    with pytest.raises(RuntimeError, match="gleaning failure"):
        await GraphExtractor(
            ontology=graph_ontology, client=client, gleaning=True
        ).transform([chunk])
    assert chunk == before


async def test_response_shape_errors_fail_fast(graph_ontology, client, chunk):
    payload = {
        "entities": [{"name": "张三"}],
        "relationships": [],
    }
    # 替身履行 InstructionClient 的 response_model 解析合同。
    client.structured_complete.side_effect = lambda **kwargs: (
        GraphExtraction.model_validate(payload)
    )
    with pytest.raises(ValidationError):
        await GraphExtractor(ontology=graph_ontology, client=client).transform([chunk])
    assert "graph_version" not in chunk.metadata


async def test_shared_instance_limits_model_calls_across_transforms(
        graph_ontology, client, chunk
):
    active = peak = 0

    async def generate(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return extraction(entities=[], relationships=[])

    client.structured_complete.side_effect = generate
    extractor = GraphExtractor(
        ontology=graph_ontology, client=client, max_concurrency=2, gleaning=True
    )
    chunks = [replace(chunk, chunk_id=str(index), metadata={}) for index in range(6)]
    await asyncio.gather(
        extractor.transform(chunks[:3]), extractor.transform(chunks[3:])
    )
    assert peak == 2 and active == 0
    assert client.structured_complete.await_count == 12


async def test_zero_chunks_no_calls(graph_ontology, client):
    chunks = []
    assert (
            await GraphExtractor(ontology=graph_ontology, client=client).transform(chunks)
            is chunks
    )
    client.structured_complete.assert_not_awaited()


@pytest.mark.parametrize("gleaning", [False, True])
async def test_real_instruction_client_schema_retry_and_incremental_reuse(
        graph_ontology, chunk, gleaning
):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        # 首轮缺少必填类别，让真正的 Instructor 反馈校验错误并重试。
        result = (
            {"entities": [{"name": "张三"}], "relationships": []}
            if len(requests) == 1
            else extraction().model_dump()
        )
        return httpx.Response(
            200,
            json={
                "id": "isolated",
                "object": "chat.completion",
                "created": 0,
                "model": "model",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(result, ensure_ascii=False),
                        },
                    }
                ],
                "usage": {
                    "prompt_tokens": 1,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            },
        )

    async with AsyncOpenAI(
            api_key="test",
            base_url="http://isolated/v1",
            max_retries=0,
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(respond)),
    ) as api:
        extractor = GraphExtractor(
            ontology=graph_ontology,
            client=InstructionClient(api, model="model"),
            gleaning=gleaning,
        )
        await extractor.transform([chunk])
        assert len(requests) == (3 if gleaning else 2)
        assert requests[0]["response_format"] == {"type": "json_object"}
        assert "同名不同类别不可混用" in requests[0]["messages"][0]["content"]
        assert "category" in json.dumps(requests[1]["messages"])
        assert len(chunk.metadata["entities"]) == 2
        await extractor.transform([chunk])
        assert len(requests) == (3 if gleaning else 2)


async def test_caller_cancellation_waits_for_model_cleanup(
        graph_ontology, client, chunk
):
    started = asyncio.Event()
    cleaned = asyncio.Event()

    async def generate(**kwargs):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaned.set()

    client.structured_complete.side_effect = generate
    before = copy.deepcopy(chunk)
    task = asyncio.create_task(
        GraphExtractor(ontology=graph_ontology, client=client).transform([chunk])
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert cleaned.is_set() and chunk == before
