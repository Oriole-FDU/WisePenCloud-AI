"""为检索 Chunk 生成可选的上下文描述，不修改正文与发布合同。"""

import asyncio
import json
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from hashlib import sha256

from openai import OpenAIError
from pydantic import TypeAdapter

from common.logger import warning
from common.utils.ai_clients.instruction import InstructionClient
from common.utils.markdown import OutlineFormatter, Section
from common.utils.markdown.chunking.tokenizer import TokenCounter, default_token_counter
from rag.core.schema import Chunk, Document

_STRATEGY_VERSION = "contextualizer-v1"  # 参与缓存指纹，策略变更时需升级
_metadata_adapter = TypeAdapter(dict[str, object])  # 仅序列化 JSON 可表达的 metadata


class ContextStatus(StrEnum):
    OK = "ok"
    FALLBACK = "fallback"
    FAILED = "failed"


class InvalidContextOutputError(ValueError):
    """上下文输出为空或超出预算；不包含 tokenizer 的程序错误。"""


@dataclass(frozen=True, slots=True)
class _PromptSettings:
    max_context_tokens: int
    target_context_chars: int
    max_output_tokens: int
    before_ratio: float
    outline_token_ratio: float


@dataclass(slots=True)
class _DocumentInput:
    """仅在单次 transform 中持有文档索引与 Token 计数缓存。"""

    document: Document
    chunks: list[Chunk]
    counter: TokenCounter
    sections: dict[str, Section] = field(default_factory=dict)
    chains: dict[str, list[Section]] = field(default_factory=dict)
    children: dict[str | None, list[Section]] = field(default_factory=dict)
    section_chunks: dict[str, list[int]] = field(default_factory=dict)
    counts: dict[str, int] = field(default_factory=dict)

    def count(self, text: str) -> int:
        # 同一 prompt 材料会被反复比较预算，缓存避免重复 tokenizer 开销。
        if text not in self.counts:
            self.counts[text] = self.counter.count(text)
        return self.counts[text]


def _index_document(
        document: Document, chunks: list[Chunk], counter: TokenCounter
) -> _DocumentInput:
    """索引可信结构；在线由 Common 生成，持久化输入在读取边界校验。"""
    ordered = sorted(chunks, key=lambda chunk: chunk.chunk_index)
    data = _DocumentInput(document, ordered, counter)
    data.sections = {
        section.section_id: section for section in document.structure.sections
    }

    children: dict[str | None, list[Section]] = defaultdict(list)
    for section in data.sections.values():
        chain: list[Section] = []
        current: Section | None = section

        # chains 统一存“根 -> 当前”，供材料选择从根向下寻找可容纳子树。
        while current is not None:
            chain.append(current)
            parent_id = current.parent_section_id
            current = data.sections[parent_id] if parent_id is not None else None

        data.chains[section.section_id] = chain[::-1]
        children[section.parent_section_id].append(section)

    data.children = dict(children)
    for siblings in data.children.values():
        # 兄弟按 ordinal 排序，邻近大纲选择才有稳定顺序。
        siblings.sort(key=lambda section: section.ordinal)

    for index, chunk in enumerate(ordered):
        if chunk.section_id is not None:
            data.section_chunks.setdefault(chunk.section_id, []).append(index)
    return data


def _select_outline(data: _DocumentInput, chunk: Chunk, budget: int) -> str:
    """在 Token 预算内优先保留目标章节父链，再补充邻近章节。"""

    def render(selected: set[str] | None = None) -> str:
        # selected=None 表示全量；否则只渲染选中的 section，但顺序仍按原文。
        sections = [
            section
            for section in data.document.structure.sections
            if selected is None or section.section_id in selected
        ]
        return OutlineFormatter(
            sections=sections, show_extra_info=False
        ).global_outline()

    full = render()
    if data.count(full) <= budget:
        return full

    # 预算放不下全文大纲时，至少保留目标 chunk 的根到当前父链。
    chain = data.chains.get(chunk.section_id, [])
    selected = {section.section_id for section in chain}
    outline = render(selected)
    if data.count(outline) > budget:
        return ""

    # 从当前节点向根逐层收集兄弟节点，优先加入距离近的兄弟。
    candidates: list[Section] = []
    for section in reversed(chain):
        siblings = data.children[section.parent_section_id]
        index = siblings.index(section)
        for distance in range(1, len(siblings)):
            if index - distance >= 0:
                candidates.append(siblings[index - distance])
            if index + distance < len(siblings):
                candidates.append(siblings[index + distance])
    candidates.extend(data.children.get(None, []))

    # 贪心加入候选；加入某 section 时连同其祖先链一起加入，保证大纲层级完整。
    for section in candidates:
        proposed = selected | {
            ancestor.section_id for ancestor in data.chains[section.section_id]
        }
        if proposed == selected:
            continue
        candidate_outline = render(proposed)
        if data.count(candidate_outline) <= budget:
            selected, outline = proposed, candidate_outline
    return outline


def _render_prompt(
        material: str,
        target: str,
        *,
        target_chars: int,
        whole_document: bool = False,
        metadata: str = "",
        outline: str = "",
) -> str:
    """全文与局部上下文使用不同标签，避免把局部片段误认为全文。"""
    if whole_document:
        # 全文模式只给 <document>，不附加局部提示，避免模型误判范围。
        parts = [f"<document>\n{material}\n</document>"]
    else:
        # 局部模式明确声明“不是全文”，并附带可选 metadata 与大纲。
        parts = ["以下是文档的局部原文，不是全文。"]
        if metadata:
            parts.append(f"<metadata>\n{metadata}\n</metadata>")
        if outline:
            parts.append(f"<outline>\n{outline}\n</outline>")
        parts.append(f"<document_part>\n{material}\n</document_part>")

    parts.extend(
        [
            f"<chunk>\n{target}\n</chunk>",
            (
                "请说明目标 chunk 在文档中的语境，以改善检索。"
                "使用与目标 chunk 相同的语言，只依据给定材料，不推测或补充外部知识。"
                f"目标约 {target_chars} 字，只返回简短上下文，不添加说明。"
            ),
        ]
    )
    return "\n\n".join(parts)


def _select_prompt(
        data: _DocumentInput, index: int, metadata: str, settings: _PromptSettings
) -> str | None:
    """按全文、祖先子树、章节正文、邻近 Chunk 的优先级选取材料。"""
    chunk = data.chunks[index]
    if not chunk.raw_text.strip():
        return None  # 空 chunk 不生成上下文，交由上层标记 fallback。

    budget = settings.max_context_tokens
    full = _render_prompt(
        data.document.text,
        chunk.raw_text,
        target_chars=settings.target_context_chars,
        whole_document=True,
    )
    # 最优先：全文能放进预算时直接使用全文，模型判断最准确。
    if data.count(full) <= budget:
        return full

    # 为局部模式预留一部分预算给大纲，帮助模型理解章节位置。
    outline = _select_outline(data, chunk, int(budget * settings.outline_token_ratio))

    def render(material: str) -> str:
        return _render_prompt(
            material,
            chunk.raw_text,
            target_chars=settings.target_context_chars,
            metadata=metadata,
            outline=outline,
        )

    # 自根向下寻找最高可容纳的祖先子树：尽量给更多相关结构，但不超预算。
    for section in data.chains.get(chunk.section_id, []):
        span = section.subtree_span
        prompt = render(data.document.text[span.start_offset: span.end_offset])
        if data.count(prompt) <= budget:
            return prompt

    lo = hi = index
    seed = chunk.raw_text
    section = data.sections.get(chunk.section_id)
    if section is not None:
        span = section.own_span
        own = data.document.text[span.start_offset: span.end_offset]
        # 若章节自身正文可容纳，则以整段章节正文为种子，并扩展到章节 chunk 边界。
        if data.count(render(own)) <= budget:
            seed = own
            indices = data.section_chunks[section.section_id]
            lo, hi = indices[0], indices[-1]

    prompt = render(seed)
    if data.count(prompt) > budget:
        return None  # 连最小种子都超预算，无法生成可靠上下文。

    seed_lo, seed_hi = lo, hi
    used_before = used_after = 0

    def expanded(left: int, right: int) -> str:
        # 不推测源码 offset，也不根据文本相同与否去重；只按 chunk 顺序拼接。
        material = "\n\n".join(
            [
                *(item.raw_text for item in data.chunks[left:seed_lo]),
                seed,
                *(item.raw_text for item in data.chunks[seed_hi + 1: right + 1]),
            ]
        )
        return render(material)

    while True:
        # 每轮尝试左扩一个 chunk、右扩一个 chunk，哪个不超预算就保留候选。
        before = expanded(lo - 1, hi) if lo > 0 else None
        after = expanded(lo, hi + 1) if hi + 1 < len(data.chunks) else None
        can_before = before is not None and data.count(before) <= budget
        can_after = after is not None and data.count(after) <= budget
        if not (can_before or can_after):
            return prompt

        # 用新增 chunk 的 token 数近似估计方向权重，避免每轮重算全量比例。
        before_tokens = (
            max(1, data.count(data.chunks[lo - 1].raw_text)) if can_before else 0
        )
        after_tokens = (
            max(1, data.count(data.chunks[hi + 1].raw_text)) if can_after else 0
        )
        go_before = can_before
        if can_before and can_after:
            # 目标是让 before token 占比接近 before_ratio；选择误差更小的方向。
            total = used_before + used_after
            error_before = abs(
                (used_before + before_tokens) / (total + before_tokens)
                - settings.before_ratio
            )
            error_after = abs(
                used_before / (total + after_tokens) - settings.before_ratio
            )
            go_before = error_before <= error_after

        if go_before:
            lo -= 1
            used_before += before_tokens
            prompt = before
        else:
            hi += 1
            used_after += after_tokens
            prompt = after


def _check_output(output: str, counter: TokenCounter, max_tokens: int) -> str:
    """验证新增上下文，复用缓存时也执行相同的检查。"""
    output = output.strip()
    # 空输出或超预算都视为失败，避免把脏数据写回 chunk.metadata。
    if not output or counter.count(output) > max_tokens:
        raise InvalidContextOutputError(
            "context output is empty or exceeds token budget"
        )
    return output


def _clear_context(chunk: Chunk, status: ContextStatus) -> None:
    # 清理旧上下文与旧指纹，只留下当前状态，防止误用过期上下文。
    chunk.metadata.pop("contextual_context", None)
    chunk.metadata.pop("context_version", None)
    chunk.metadata["context_status"] = status.value


class Contextualizer:
    """可选的 Chunk 上下文增强；并发限制共享，文档索引按调用隔离。"""

    def __init__(
            self,
            *,
            client: InstructionClient,
            token_counter: TokenCounter | None = None,
            max_context_tokens: int = 8000,
            target_context_chars: int = 100,
            max_output_tokens: int = 150,
            before_ratio: float = 0.5,
            outline_token_ratio: float = 0.15,
            max_concurrency: int = 4,
    ) -> None:
        self._client = client
        self._counter = token_counter or default_token_counter()
        self._settings = _PromptSettings(
            max_context_tokens=max_context_tokens,
            target_context_chars=target_context_chars,
            max_output_tokens=max_output_tokens,
            before_ratio=before_ratio,
            outline_token_ratio=outline_token_ratio,
        )
        # 同一实例内所有 chunk 共享并发上限，避免一次 transform 打爆模型。
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def transform(
            self, chunks: list[Chunk], *, document: Document | None = None
    ) -> list[Chunk]:
        if not chunks:
            return chunks
        if document is None:
            raise ValueError("contextualization requires a document")

        # 索引与缓存只服务本次 transform，不跨调用共享，避免文档间污染。
        data = _index_document(document, chunks, self._counter)
        # metadata 合同错误直接传播，不当作可选模型失败继续发布。
        serialized = _metadata_adapter.dump_python(document.metadata, mode="json")
        metadata = (
            json.dumps(
                serialized,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if serialized
            else ""
        )

        # 固定配置只序列化一次，各 Chunk 的指纹仅随 prompt 改变。
        fingerprint_data = {
            "strategy": _STRATEGY_VERSION,
            "model": self._client.model,
            "tokenizer": self._counter.name,
            "settings": asdict(self._settings),
        }

        async def process(index: int, chunk: Chunk) -> None:
            prompt = _select_prompt(data, index, metadata, self._settings)
            if prompt is None:
                _clear_context(chunk, ContextStatus.FALLBACK)
                return

            # 指纹覆盖策略、模型、tokenizer、参数与 prompt；任一变化都应重算。
            fingerprint = sha256(
                json.dumps(
                    {**fingerprint_data, "prompt": prompt},
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            previous = chunk.metadata.get("contextual_context")
            # 只有状态 OK、指纹一致且旧输出仍有效时才跳过模型调用。
            if (
                    chunk.metadata.get("context_status") == ContextStatus.OK.value
                    and chunk.metadata.get("context_version") == fingerprint
            ):
                try:
                    if not isinstance(previous, str):
                        raise InvalidContextOutputError(
                            "cached context must be a string"
                        )
                    _check_output(
                        previous, self._counter, self._settings.max_output_tokens
                    )
                except InvalidContextOutputError as exc:
                    warning(
                        "invalid_cached_content",
                        chunk_id=chunk.chunk_id,
                        cache_type="context",
                        reason=str(exc),
                    )
                else:
                    return

            async with self._semaphore:
                try:
                    output = await self._client.complete(
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=self._settings.max_output_tokens,
                    )
                except (OpenAIError, OSError, ValueError) as exc:
                    # Common 用 ValueError 表示空响应/截断；仅在模型调用处隔离。
                    warning(
                        "context_generation_failed",
                        chunk_id=chunk.chunk_id,
                        error=str(exc),
                    )
                    _clear_context(chunk, ContextStatus.FAILED)
                    return

            try:
                context = _check_output(
                    output, self._counter, self._settings.max_output_tokens
                )
            except InvalidContextOutputError as exc:
                warning(
                    "context_output_failed", chunk_id=chunk.chunk_id, error=str(exc)
                )
                _clear_context(chunk, ContextStatus.FAILED)
                return

            # 成功时原地写回 metadata；正文与发布合同不变。
            chunk.metadata.update(
                contextual_context=context,
                context_status=ContextStatus.OK.value,
                context_version=fingerprint,
            )

        # 所有 chunk 并发处理，但每个模型调用受 semaphore 限制。
        await asyncio.gather(
            *(process(index, chunk) for index, chunk in enumerate(data.chunks))
        )
        return chunks
