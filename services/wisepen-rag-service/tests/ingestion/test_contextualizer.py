"""完整提示词预算、材料选择、可选失败和生成指纹的行为验收。"""

import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from common.utils.markdown import Section, SourceSpan
from common.utils.markdown.chunking.tokenizer import default_token_counter
from pydantic_core import PydanticSerializationError

from rag.core.schema import Chunk, Document, DocumentStructure
from rag.ingestion.transformers import Contextualizer
from rag.ingestion.transformers.contextualizer import (
    _index_document,
    _render_prompt,
    _select_outline,
)


class CharacterCounter:
    name = "test:characters"

    def count(self, text):
        return len(text)


def make_input(*texts, sections=None, metadata=None):
    text = "\n\n".join(texts)
    document = Document(
        "resource",
        1,
        "revision",
        text,
        DocumentStructure(len(text), sections or []),
        metadata or {},
    )
    chunks = [
        Chunk(
            f"chunk-{index}",
            "resource",
            "revision",
            index,
            None,
            [],
            part,
            len(part),
            [],
            {},
        )
        for index, part in enumerate(texts)
    ]
    return document, chunks


def section(identity, start, own_end, end, *, parent=None, ordinal=0, title=None):
    return Section(
        section_id=identity,
        title=title or identity,
        level=1 if parent is None else 2,
        parent_section_id=parent,
        ordinal=ordinal,
        section_path=[identity],
        own_span=SourceSpan(start, own_end),
        subtree_span=SourceSpan(start, end),
    )


@pytest.fixture
def client():
    return SimpleNamespace(
        model="model", complete=AsyncMock(return_value="  本段说明文档主题。  ")
    )


def transformer(client, **kwargs):
    kwargs.setdefault("token_counter", CharacterCounter())
    return Contextualizer(client=client, **kwargs)


def prompt_for(client, target):
    return next(
        call.kwargs["messages"][0]["content"]
        for call in client.complete.await_args_list
        if f"<chunk>\n{target}\n</chunk>" in call.kwargs["messages"][0]["content"]
    )


def material(prompt):
    return prompt.split("<document_part>\n", 1)[1].split("\n</document_part>", 1)[0]


async def test_whole_prompt_preserves_facts_order_and_incrementally_reuses(client):
    doc, chunks = make_input(
        " 第一块 ", "第二块", metadata={"title": "ignored in whole prompt"}
    )
    before = deepcopy(chunks)
    service = transformer(client)
    assert await service.transform(chunks, document=doc) is chunks
    assert [replace(chunk, metadata={}) for chunk in chunks] == before
    assert client.complete.await_count == 2
    prompt = prompt_for(client, chunks[0].raw_text)
    assert f"<document>\n{doc.text}\n</document>" in prompt
    assert "<outline>" not in prompt and "<metadata>" not in prompt
    assert "相同的语言" in prompt and "100 字" in prompt
    assert client.complete.await_args.kwargs["max_tokens"] == 150
    assert all(chunk.metadata["context_status"] == "ok" for chunk in chunks)
    assert all(len(chunk.metadata["context_version"]) == 64 for chunk in chunks)
    await service.transform(chunks, document=doc)
    assert client.complete.await_count == 2
    await transformer(client, max_concurrency=1).transform(chunks, document=doc)
    assert client.complete.await_count == 2  # 并发数不参与生成指纹。


@pytest.mark.parametrize(
    "change",
    [
        "model",
        "tokenizer",
        "budget",
        "target",
        "output",
        "ratio",
        "outline",
        "material",
    ],
)
async def test_generation_affecting_changes_invalidate_fingerprint(client, change):
    doc, chunks = make_input("正文")
    await transformer(client).transform(chunks, document=doc)
    kwargs = {}
    if change == "model":
        client.model = "new-model"
    elif change == "tokenizer":
        counter = CharacterCounter()
        counter.name = "different-counter"
        kwargs["token_counter"] = counter
    elif change == "material":
        doc.text += "\n更多信息"
        doc.structure.total_length = len(doc.text)
    else:
        name, value = {
            "budget": ("max_context_tokens", 7000),
            "target": ("target_context_chars", 99),
            "output": ("max_output_tokens", 149),
            "ratio": ("before_ratio", 0.4),
            "outline": ("outline_token_ratio", 0.2),
        }[change]
        kwargs[name] = value
    await transformer(client, **kwargs).transform(chunks, document=doc)
    assert client.complete.await_count == 2


async def test_highest_fitting_ancestor_and_deterministic_json_metadata(client):
    parts = [
        "# Root\nRoot body",
        "## Current\nTarget",
        "### Child\nNested",
        "# Outside\n" + "x" * 1500,
    ]
    doc, chunks = make_input(
        *parts,
        metadata={"中文": "值", "date": datetime(2026, 1, 1, tzinfo=UTC)},
    )
    child_start = doc.text.index("## Current")
    nested_start = doc.text.index("### Child")
    outside_start = doc.text.index("# Outside")
    doc.structure.sections = [
        section("root", 0, child_start, outside_start),
        section("current", child_start, nested_start, outside_start, parent="root"),
        section("nested", nested_start, outside_start, outside_start, parent="current"),
        section("outside", outside_start, len(doc.text), len(doc.text), ordinal=1),
    ]
    chunks[1].section_id = "current"
    await transformer(client, max_context_tokens=600).transform(chunks, document=doc)
    prompt = prompt_for(client, parts[1])
    assert material(prompt) == doc.text[:outside_start]
    assert "不是全文" in prompt and "<outline>" in prompt
    encoded = prompt.split("<metadata>\n", 1)[1].split("\n</metadata>", 1)[0]
    assert encoded == '{"date":"2026-01-01T00:00:00Z","中文":"值"}'
    assert json.loads(encoded)["中文"] == "值"
    before = client.complete.await_count
    doc.metadata["中文"] = "变化"
    await transformer(client, max_context_tokens=600).transform(chunks, document=doc)
    assert client.complete.await_count > before


async def test_own_span_seed_keeps_entire_direct_content_not_chunk_reconstruction(
    client,
):
    own = "# Parent\n\n完整直属内容\n\n"
    child = "## Huge child\n" + "x" * 1800
    doc, chunks = make_input(own, child)
    child_start = len(own) + 2
    doc.structure.sections = [
        section("parent", 0, child_start, len(doc.text)),
        section("child", child_start, len(doc.text), len(doc.text), parent="parent"),
    ]
    chunks[0].section_id = "parent"
    chunks[0].raw_text = "规范化后的直属内容"
    await transformer(client, max_context_tokens=400).transform(chunks, document=doc)
    assert material(prompt_for(client, chunks[0].raw_text)) == doc.text[:child_start]
    assert "contextual_context" not in chunks[1].metadata
    assert chunks[1].metadata["context_status"] == "fallback"


@pytest.mark.parametrize(
    "ratio,expected", [(0, "after"), (0.5, "before"), (1, "before")]
)
async def test_window_direction_and_full_prompt_boundary(client, ratio, expected):
    texts = ["before" * 7, "seed", "after" * 8, "x" * 1500]
    doc, chunks = make_input(*texts)
    # 预算允许一个邻居，不能同时装入两个；种子不参与方向比例。
    limit = max(
        len(_render_prompt(f"{texts[0]}\n\nseed", "seed", target_chars=100)),
        len(_render_prompt(f"seed\n\n{texts[2]}", "seed", target_chars=100)),
    )
    await transformer(client, max_context_tokens=limit, before_ratio=ratio).transform(
        chunks, document=doc
    )
    prompt = prompt_for(client, "seed")
    assert len(prompt) <= limit
    assert (texts[0] in material(prompt)) == (expected == "before")
    assert (texts[2] in material(prompt)) == (expected == "after")


async def test_blocked_side_does_not_prevent_other_side_or_deduplicate_equal_text(
    client,
):
    doc, chunks = make_input("x" * 1500, "same", "same", "tail", "y" * 1500)
    limit = len(_render_prompt("same\n\nsame\n\ntail", "same", target_chars=100))
    await transformer(client, max_context_tokens=limit, before_ratio=1).transform(
        chunks, document=doc
    )
    prompts = [
        call.kwargs["messages"][0]["content"]
        for call in client.complete.await_args_list
    ]
    assert any(material(prompt) == "same\n\nsame\n\ntail" for prompt in prompts)


async def test_window_crosses_section_boundaries(client):
    doc, chunks = make_input("before", "target", "after", "x" * 1200)
    # 当前 own 也过大，走目标种子；全局窗口可以访问相邻 section。
    doc.structure.sections = [section("s", 0, len(doc.text), len(doc.text))]
    chunks[1].section_id = "s"
    limit = len(_render_prompt("before\n\ntarget\n\nafter", "target", target_chars=100))
    await transformer(
        client, max_context_tokens=limit, outline_token_ratio=0
    ).transform(chunks, document=doc)
    assert material(prompt_for(client, "target")) == "before\n\ntarget\n\nafter"


def test_outline_reduction_preserves_ancestors_then_near_siblings():
    doc, chunks = make_input("z" * 100)
    doc.structure.sections = (
        [section("root", 0, 1, 100)]
        + [
            section(
                f"child-{index}",
                index + 1,
                index + 2,
                index + 2,
                parent="root",
                ordinal=index,
            )
            for index in range(12)
        ]
        + [section("outside", 99, 100, 100, ordinal=1)]
    )
    chunks[0].section_id = "child-5"
    data = _index_document(doc, chunks, CharacterCounter())
    outline = _select_outline(data, chunks[0], 40)
    assert outline.startswith("# root\n") and "## child-5" in outline
    assert "## child-4" in outline and "## child-6" in outline
    assert "child-0" not in outline and len(outline) <= 40
    assert _select_outline(data, chunks[0], 3) == ""


@pytest.mark.parametrize("failure", ["empty", "overbudget", "timeout", "truncated"])
async def test_model_failures_clear_stale_context_and_retry(client, failure):
    doc, chunks = make_input("正文")
    chunks[0].metadata.update(
        contextual_context="旧上下文", context_version="old", custom=True
    )
    if failure in ("timeout", "truncated"):
        client.complete.side_effect = (
            TimeoutError() if failure == "timeout" else ValueError("truncated")
        )
    else:
        client.complete.return_value = " " if failure == "empty" else "x" * 151
    service = transformer(client)
    await service.transform(chunks, document=doc)
    assert chunks[0].metadata == {"context_status": "failed", "custom": True}
    client.complete.side_effect = None
    client.complete.return_value = "上下文"
    await service.transform(chunks, document=doc)
    assert chunks[0].metadata["context_status"] == "ok"
    assert client.complete.await_count == 2


@pytest.mark.parametrize("case", ["empty", "chunk-too-large", "metadata-too-large"])
async def test_no_model_downgrades_and_can_retry(client, case):
    doc, chunks = make_input("正文")
    budget = 300
    expected = "fallback"
    if case == "empty":
        chunks[0].raw_text = " "
    elif case == "chunk-too-large":
        chunks[0].raw_text = "x" * 300
    elif case == "metadata-too-large":
        doc.text = "x" * 1000
        doc.structure.total_length = 1000
        doc.metadata["big"] = "x" * 1000
    chunks[0].metadata.update(contextual_context="old", context_version="old")
    await transformer(client, max_context_tokens=budget).transform(chunks, document=doc)
    assert chunks[0].metadata == {"context_status": expected}
    client.complete.assert_not_awaited()
    doc.metadata.clear()
    chunks[0].raw_text = "正文"
    await transformer(client).transform(chunks, document=doc)
    assert chunks[0].metadata["context_status"] == "ok"


async def test_empty_input_missing_document_and_cancellation(client):
    service = transformer(client)
    empty = []
    assert await service.transform(empty) is empty
    doc, chunks = make_input("正文")
    with pytest.raises(ValueError, match="document"):
        await service.transform(chunks)
    client.complete.side_effect = asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await service.transform(chunks, document=doc)


async def test_metadata_serialization_error_stops_before_model_without_mutation(client):
    doc, chunks = make_input("正文", metadata={"unsupported": object()})
    chunks[0].metadata.update(contextual_context="旧上下文", context_version="old")
    previous = deepcopy(chunks)
    with pytest.raises(PydanticSerializationError):
        await transformer(client).transform(chunks, document=doc)
    assert chunks == previous
    client.complete.assert_not_awaited()
    assert "context_status" not in chunks[0].metadata


async def test_shared_concurrency_limit_and_document_isolation(client):
    active = peak = 0

    async def complete(**kwargs):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return "上下文"

    client.complete.side_effect = complete
    first, one = make_input("first-1", "first-2", "first-3")
    second, two = make_input("second-1", "second-2", "second-3")
    second.resource_id = "second"
    for chunk in two:
        chunk.resource_id = "second"
    service = transformer(client, max_concurrency=2)
    await asyncio.gather(
        service.transform(one, document=first), service.transform(two, document=second)
    )
    assert peak == 2 and active == 0
    assert "second-" not in prompt_for(client, "first-1")
    assert "first-" not in prompt_for(client, "second-1")


async def test_real_bpe_full_prompt_budget_boundary(client):
    counter = default_token_counter()
    doc, chunks = make_input("中文 BPE 预算😀。")
    prompt = _render_prompt(
        doc.text, chunks[0].raw_text, target_chars=100, whole_document=True
    )
    budget = counter.count(prompt)
    service = Contextualizer(
        client=client, token_counter=counter, max_context_tokens=budget
    )
    await service.transform(chunks, document=doc)
    assert prompt_for(client, chunks[0].raw_text) == prompt
    client.complete.reset_mock()
    await Contextualizer(
        client=client, token_counter=counter, max_context_tokens=budget - 1
    ).transform(chunks, document=doc)
    assert all(
        counter.count(call.kwargs["messages"][0]["content"]) <= budget - 1
        for call in client.complete.await_args_list
    )


async def test_nonadditive_counter_rechecks_actual_prompt_before_adding_neighbor(
    client,
):
    class NonadditiveCounter(CharacterCounter):
        name = "test:nonadditive"

        def count(self, text):
            # 单位分别计数无法看出组合后的超限，模拟 BPE 的边界效应。
            return len(text) + (1000 if "left\n\nseed" in text else 0)

    doc, chunks = make_input("left", "seed", "right", "x" * 1000)
    budget = len(_render_prompt("seed\n\nright", "seed", target_chars=100))
    counter = NonadditiveCounter()
    await Contextualizer(
        client=client, token_counter=counter, max_context_tokens=budget
    ).transform(chunks, document=doc)
    prompt = prompt_for(client, "seed")
    assert material(prompt) == "seed\n\nright"
    assert counter.count(prompt) <= budget


async def test_one_chunk_failure_keeps_other_success_and_program_errors_propagate(
    client,
):
    doc, chunks = make_input("first", "second")
    client.complete.side_effect = [TimeoutError("first failed"), "第二块上下文"]
    await transformer(client).transform(chunks, document=doc)
    assert [chunk.metadata["context_status"] for chunk in chunks] == ["failed", "ok"]
    client.complete.side_effect = TypeError("incorrect client invocation")
    with pytest.raises(TypeError, match="invocation"):
        await transformer(client).transform(chunks[:1], document=doc)


async def test_sorted_material_index_does_not_reorder_callers_list(client):
    doc, chunks = make_input("before", "target", "after", "x" * 1500)
    chunks.reverse()
    ids = [chunk.chunk_id for chunk in chunks]
    budget = len(
        _render_prompt("before\n\ntarget\n\nafter", "target", target_chars=100)
    )
    await transformer(client, max_context_tokens=budget).transform(chunks, document=doc)
    assert [chunk.chunk_id for chunk in chunks] == ids
    assert material(prompt_for(client, "target")) == "before\n\ntarget\n\nafter"


async def test_invalid_cached_output_with_matching_fingerprint_is_regenerated(client):
    doc, chunks = make_input("正文")
    service = transformer(client)
    await service.transform(chunks, document=doc)
    chunks[0].metadata["contextual_context"] = "x" * 151
    await service.transform(chunks, document=doc)
    assert client.complete.await_count == 2
    assert chunks[0].metadata["contextual_context"] == "本段说明文档主题。"


async def test_invalid_context_cache_logs_reason_but_normal_miss_is_quiet(
    client, monkeypatch
):
    warning = Mock()
    monkeypatch.setattr("rag.ingestion.transformers.contextualizer.warning", warning)
    doc, chunks = make_input("正文")
    service = transformer(client)
    await service.transform(chunks, document=doc)
    chunks[0].metadata["context_version"] = "old"
    await service.transform(chunks, document=doc)
    warning.assert_not_called()
    chunks[0].metadata["contextual_context"] = "x" * 151
    await service.transform(chunks, document=doc)
    assert warning.call_args.kwargs["cache_type"] == "context"
    assert warning.call_args.kwargs["chunk_id"] == chunks[0].chunk_id
    assert "budget" in warning.call_args.kwargs["reason"]


@pytest.mark.parametrize("error_type", [RuntimeError, TypeError])
async def test_model_program_errors_propagate_without_failed_status(client, error_type):
    doc, chunks = make_input("正文")
    client.complete.side_effect = error_type("program error")
    with pytest.raises(error_type):
        await transformer(client).transform(chunks, document=doc)
    assert "context_status" not in chunks[0].metadata


async def test_tokenizer_value_error_is_not_treated_as_invalid_output(client):
    class BrokenCounter(CharacterCounter):
        def count(self, text):
            if text == "本段说明文档主题。":
                raise ValueError("tokenizer program error")
            return super().count(text)

    doc, chunks = make_input("正文")
    with pytest.raises(ValueError, match="tokenizer program error"):
        await transformer(client, token_counter=BrokenCounter()).transform(
            chunks, document=doc
        )
    assert "context_status" not in chunks[0].metadata
