from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from pathlib import PurePath
from typing import Dict, List, Optional, Set, Tuple

from chat.application.algorithms.ranking.fielded_bm25 import score_fielded_bm25, FieldedItem
from chat.application.algorithms.ranking.tokenizer import tokenize_for_bm25
from chat.application.infra.content_store.models import ContentChunk, StoredContent
from chat.application.tools.common.evidence_ranking.models import (
    EvidenceRankResult, RankedEvidence, EvidenceTermHitStat, EvidenceFieldHitStat,
)
from chat.application.tools.tool_content_store import ToolContentStore
from chat.application.tools.web.services.web_search.utils.notes import add_note
from common.logger import log_event

MAX_CHUNKS_PER_CONTENT = 5
EXCERPT_MAX_CHARS = 300

# 用来构造稳定排序顺序，避免不同 content 的局部顺序冲突
FIRST_SEEN_CONTENT_STRIDE = 100_000

FIELD_WEIGHTS_CHUNK = {"title": 3.0, "heading": 2.0, "body": 1.0}
FIELD_WEIGHTS_WEB_SEARCH = {"title": 3.0, "domain": 1.0, "snippet": 1.0}


@dataclass(frozen=True, slots=True)
class EvidenceCandidate:
    content_id: str
    chunk_index: int
    evidence_type: str
    score: float
    original_rank: int
    title: str
    source: str
    url: str
    domain: str
    source_id: str
    excerpt_source_text: str
    start_offset: int
    end_offset: int
    term_hit_stats: Tuple[EvidenceTermHitStat, ...]
    matched_reason: str
    first_seen_order: int


@dataclass(frozen=True, slots=True)
class ChunkEvidenceMeta:
    content_id: str
    chunk_index: int
    title: str
    source: str
    url: str
    chunk_text: str
    start_offset: int
    end_offset: int
    first_seen_order: int
    heading_path: Tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WebEvidenceMeta:
    content_id: str
    source_id: str
    title: str
    url: str
    domain: str
    snippet: str
    first_seen_order: int


@dataclass(frozen=True, slots=True)
class RankPartialResult:
    candidates: Tuple[EvidenceCandidate, ...]
    total_scanned: int


def rank_evidence(
    query: str,
    content_ids: List[str],
    session_id: str,
    content_store: ToolContentStore,
    max_evidence: int = 8,
    max_chunks_per_content: int = MAX_CHUNKS_PER_CONTENT,
    before: int = 0,
    after: int = 0,
    max_context_chars_per_hit: int = 2000,
) -> EvidenceRankResult:
    """我要用 query，在这些 content_ids 里找最多 max_evidence 条证据。"""

    # ── Step 1: 从注入的 ToolContentStore 缓存里批量取内容 ──
    found: Dict[str, StoredContent] = {}  # 成功取到的内容
    missing: List[str] = []  # 缓存里找不到的 id
    notes: List[str] = []  # 供调试用的备注，最终塞进结果
    resolved_content_ids: List[str] = []

    # 根据 content_id 从缓存里面获取内容
    # 如果取到，计入 found，否则计入 missing
    for cid in content_ids:
        resolved_cid, redirect_note = content_store.canonicalize_content_id(
            content_id=cid,
            session_id=session_id,
        )
        if redirect_note:
            add_note(notes, redirect_note)
        if resolved_cid not in resolved_content_ids:
            resolved_content_ids.append(resolved_cid)

        stored = content_store.get(content_id=resolved_cid, session_id=session_id)
        if stored is not None:
            found[resolved_cid] = stored
        else:
            missing.append(resolved_cid)

    if missing:
        add_note(notes, f"content_id not found or expired: {', '.join(missing)}")

    # 全部 miss → 直接返回空结果，不用走后续逻辑
    if not found:
        return EvidenceRankResult(
            query=query,
            total_chunks_scanned=0,
            content_ids_found=[],
            content_ids_missing=missing,
            notes=notes,
        )

    # ── Step 2: 对 query 分词，后续 BM25 和命中统计都用这个 ──
    # dict.fromkeys 去重同时保留顺序
    query_terms = tuple(dict.fromkeys(tokenize_for_bm25(query)))

    # content_order: {content_id -> 原始传入顺序}，用于同分时的稳定排序
    content_order = {cid: i for i, cid in enumerate(resolved_content_ids) if cid in found}

    # 先多选一倍候选，再在域名/块数限制后截取 max_evidence
    candidate_window = max_evidence * 2

    # ── Step 3: 按内容类型分路排序 ──
    web_search_items = {cid: s for cid, s in found.items() if s.metadata.get("content_kind") == "web_search_evidence_pack"}
    generic_items = {cid: s for cid, s in found.items() if cid not in web_search_items}

    candidates: List[EvidenceCandidate] = []
    total_scanned = 0

    if web_search_items:
        result = _rank_web_search_evidence(
            query=query, contents=web_search_items, max_evidence=candidate_window,
            notes=notes, query_terms=query_terms, content_order=content_order,
        )
        candidates.extend(result.candidates)
        total_scanned += result.total_scanned

    if generic_items:
        result = _rank_generic_content_chunks(
            query=query, contents=generic_items, max_evidence=candidate_window,
            max_chunks_per_content=max_chunks_per_content, notes=notes,
            query_terms=query_terms, content_order=content_order,
        )
        candidates.extend(result.candidates)
        total_scanned += result.total_scanned

    # ── Step 4: 全局排序：先按 score 降序，同分按 first_seen_order 升序（先出现优先）──
    sorted_candidates = sorted(candidates, key=lambda c: (-c.score, c.first_seen_order))

    # ── Step 5: 过滤掉没有词汇命中的候选（score == 0），取 top-N ──
    positive = [c for c in sorted_candidates if c.score > 0.0]

    if candidates and not positive:
        add_note(notes, "No positive lexical match found in ranked candidates.")

    top_candidates = positive[:max_evidence]

    # ── Step 6: 把候选转成最终结果对象，顺便生成摘要 ──
    evidence_list = [
        RankedEvidence(
            content_id=c.content_id,
            chunk_index=c.chunk_index,
            score=c.score,
            rank=c.original_rank,
            title=c.title,
            source=c.source, url=c.url,
            excerpt=_make_excerpt(c.excerpt_source_text, query_terms=query_terms),
            start_offset=c.start_offset,
            end_offset=c.end_offset,
            source_id=c.source_id,
            domain=c.domain,
            evidence_type=c.evidence_type,
            matched_reason=c.matched_reason,
            term_hit_stats=c.term_hit_stats,
            context_preview=_build_context_preview(
                candidate=c,
                content_store=content_store,
                session_id=session_id,
                before=before,
                after=after,
                max_chars=max_context_chars_per_hit,
            ),
        )
        for c in top_candidates
    ]

    log_event(
        "evidence ranking 完成",
        query=query, total_chunks_scanned=total_scanned,
        sources_with_evidence=len({ev.content_id for ev in evidence_list}),
        evidence_snippets=len(evidence_list), max_evidence=max_evidence,
    )

    return EvidenceRankResult(
        query=query,
        evidence=evidence_list,
        total_chunks_scanned=total_scanned,
        content_ids_found=list(found.keys()),
        content_ids_missing=missing,
        notes=notes,
    )


def _rank_documents(
    documents: List[FieldedItem],
    scores: Dict[str, float],
) -> List[Tuple[str, float, int]]:
    """BM25 分数降序排列，同分按原始顺序稳定排。"""
    ordered = sorted(enumerate(documents), key=lambda x: (-scores.get(x[1].id, 0.0), x[0]))
    return [(d.id, scores.get(d.id, 0.0), rank) for rank, (_, d) in enumerate(ordered)]


def _rank_generic_content_chunks(
    *,
    query: str,
    contents: Dict[str, StoredContent],
    max_evidence: int,
    max_chunks_per_content: int,
    notes: List[str],
    query_terms: Tuple[str, ...],
    content_order: Dict[str, int],
) -> RankPartialResult:
    """
    处理笔记、PDF、上传文档等普通内容，
    核心是把每个 chunk 展开成 FieldedItem（带字段权重的 BM25 文档）。
    """

    documents: List[FieldedItem] = []   # 进入 BM25 的文档列表
    chunk_meta: Dict[str, ChunkEvidenceMeta] = {}   # doc_id -> metadata

    for cid, stored in contents.items():
        title = stored.metadata.get("title") or stored.metadata.get("display_name") or ""
        # 没有 title，fallback 到文件路径
        if not title:
            src = stored.source or ""
            p = PurePath(src)
            title = p.name if (p.is_absolute() or src.startswith("/")) else src

        source = stored.source or ""
        url = stored.metadata.get("urls", "") or ""
        content_seen_order = content_order.get(cid, len(content_order))
        local_order = 0

        # 没有分块信息 -> 整个文档当作一个块处理
        if not stored.chunks:
            doc_id = f"{cid}:-1"
            chunk_text = stored.text or ""
            if chunk_text.strip():
                h_path = _extract_heading_path(None, stored)
                documents.append(FieldedItem(id=doc_id, fields={"title": title, "heading": " > ".join(h_path), "body": chunk_text}))
                chunk_meta[doc_id] = ChunkEvidenceMeta(
                    content_id=cid,
                    chunk_index=-1,
                    title=title,
                    source=source,
                    url=url,
                    chunk_text=chunk_text,
                    start_offset=0,
                    end_offset=len(chunk_text),
                    first_seen_order=content_seen_order * FIRST_SEEN_CONTENT_STRIDE, heading_path=h_path,
                )
        else:
            # 有分块 -> 每个 chunk 生成一个 FieldedItem
            for chunk in stored.chunks:
                chunk_text = stored.text[chunk.start_offset:chunk.end_offset] if chunk.start_offset >= 0 else ""
                if not chunk_text.strip():
                    continue

                doc_id = f"{cid}:{chunk.index}"
                h_path = _extract_heading_path(chunk, stored)
                documents.append(FieldedItem(id=doc_id, fields={"title": title, "heading": " > ".join(h_path), "body": chunk_text}))
                chunk_meta[doc_id] = ChunkEvidenceMeta(
                    content_id=cid,
                    chunk_index=chunk.index,
                    title=title,
                    source=source,
                    url=url,
                    chunk_text=chunk_text,
                    start_offset=chunk.start_offset,
                    end_offset=chunk.end_offset,
                    first_seen_order=content_seen_order * FIRST_SEEN_CONTENT_STRIDE + local_order,
                    heading_path=h_path,
                )
                local_order += 1

    total_scanned = len(documents)

    if not documents:
        add_note(notes, "No readable chunks found in the specified content.")
        return RankPartialResult(candidates=(), total_scanned=0)

    # ── 去重：把 body 归一化空白后用集合过滤完全相同的 chunk ──
    seen_texts: Set[str] = set()
    deduped: List[FieldedItem] = []
    for doc in documents:
        norm = " ".join(doc.fields["body"].split())
        if norm not in seen_texts:
            seen_texts.add(norm)
            deduped.append(doc)

    dedup_hit = total_scanned - len(deduped)
    if dedup_hit > 0:
        add_note(notes, f"Skipped {dedup_hit} duplicate chunk(s) with identical normalized text.")

    # ── BM25 打分：3 个字段权重不同：title(3.0) > heading(2.0) > body(1.0) ──
    scores_by_id = score_fielded_bm25(query, deduped, FIELD_WEIGHTS_CHUNK)
    ranked_tuples = _rank_documents(deduped, scores_by_id)

    scores = [s for _, s, _ in ranked_tuples]

    # ── 每个 content_id 最多取 max_chunks_per_content 块（默认 5）──
    per_content_count: Dict[str, int] = {}
    candidates: List[EvidenceCandidate] = []
    max_chunks_skipped = 0

    for doc_id, score, rank in ranked_tuples:
        meta = chunk_meta.get(doc_id)
        if meta is None:
            continue

        count = per_content_count.get(meta.content_id, 0)
        if count >= max_chunks_per_content:
            max_chunks_skipped += 1
            continue

        field_texts = {"title": meta.title, "heading": " > ".join(meta.heading_path), "body": meta.chunk_text}
        term_hit_stats = _build_inline_hit_stats(query_terms, field_texts)

        candidates.append(EvidenceCandidate(
            content_id=meta.content_id,
            chunk_index=meta.chunk_index,
            evidence_type="chunk",
            score=score,
            original_rank=rank,
            title=meta.title,
            source=meta.source,
            url=meta.url,
            domain="",
            source_id="",
            excerpt_source_text=meta.chunk_text,
            start_offset=meta.start_offset,
            end_offset=meta.end_offset,
            term_hit_stats=term_hit_stats,
            matched_reason=_build_matched_reason_text("chunk", term_hit_stats),
            first_seen_order=meta.first_seen_order,
        ))
        per_content_count[meta.content_id] = count + 1

        if len(candidates) >= max_evidence:
            break

    if max_chunks_skipped > 0:
        add_note(notes, f"Skipped {max_chunks_skipped} chunk(s) after max_chunks_per_content={max_chunks_per_content}.")

    return RankPartialResult(candidates=tuple(candidates), total_scanned=total_scanned)


def _rank_web_search_evidence(
    *,
    query: str,
    contents: Dict[str, StoredContent],
    max_evidence: int,
    notes: List[str],
    query_terms: Tuple[str, ...],
    content_order: Dict[str, int],
) -> RankPartialResult:
    documents: List[FieldedItem] = []
    meta: Dict[str, WebEvidenceMeta] = {}

    for cid, stored in contents.items():
        try:
            payload = json.loads(stored.text)
        except json.JSONDecodeError:
            add_note(notes, f"web_search_evidence_pack is not valid JSON: {cid}")
            continue

        results = payload.get("results")
        if not isinstance(results, list):
            add_note(notes, f"web_search_evidence_pack missing results array: {cid}")
            continue

        content_seen_order = content_order.get(cid, len(content_order))

        for local_order, item in enumerate(results):
            if not isinstance(item, dict):
                continue

            source_id, title = item.get("source_id"), item.get("title")
            url, domain, snippet = item.get("urls"), item.get("domain"), item.get("snippet")

            if not all(isinstance(v, str) for v in [source_id, title, url, domain, snippet]) or not source_id or not url:
                continue

            fields = {"title": title, "domain": domain, "snippet": snippet}
            if not any(v.strip() for v in fields.values()):
                continue

            doc_id = f"{cid}:source:{source_id}"
            documents.append(
                FieldedItem(
                    id=doc_id,
                    fields=fields
                )
            )
            meta[doc_id] = WebEvidenceMeta(
                content_id=cid,
                source_id=source_id,
                title=title,
                url=url,
                domain=domain,
                snippet=snippet,
                first_seen_order=content_seen_order * FIRST_SEEN_CONTENT_STRIDE + local_order,
            )

    if not documents:
        add_note(notes, "No readable web_search results found in the specified content.")
        return RankPartialResult(candidates=(), total_scanned=0)

    scores_by_id = score_fielded_bm25(query, documents, FIELD_WEIGHTS_WEB_SEARCH)

   # -> [(doc_id, score, rank)]
    ranked_tuples = _rank_documents(documents, scores_by_id)

    candidates: List[EvidenceCandidate] = []
    same_domain_count: Dict[str, int] = {}
    same_domain_skipped = 0

    for doc_id, score, rank in ranked_tuples:
        data = meta.get(doc_id)
        if data is None:
            continue

        domain = data.domain
        domain_count = same_domain_count.get(domain, 0)
        if domain and domain_count >= 2:
            same_domain_skipped += 1
            continue

        field_texts = {"title": data.title, "domain": data.domain, "snippet": data.snippet}
        term_hit_stats = _build_inline_hit_stats(query_terms, field_texts)

        candidates.append(EvidenceCandidate(
            content_id=data.content_id,
            chunk_index=-1,
            score=score,
            original_rank=rank,
            title=data.title,
            source=data.domain,
            url=data.url,
            domain=data.domain,
            source_id=data.source_id,
            excerpt_source_text=data.snippet,
            start_offset=0,
            end_offset=0,
            evidence_type="web_search_result",
            term_hit_stats=term_hit_stats,
            matched_reason=_build_matched_reason_text("web_search_result", term_hit_stats),
            first_seen_order=data.first_seen_order,
        ))

        if domain:
            same_domain_count[domain] = domain_count + 1

        if len(candidates) >= max_evidence:
            break

    if same_domain_skipped > 0:
        add_note(notes, f"Skipped {same_domain_skipped} web result(s) after same-domain cap of 2.")

    return RankPartialResult(candidates=tuple(candidates), total_scanned=len(documents))


def _build_inline_hit_stats(
    query_terms: Tuple[str, ...],
    field_texts: Dict[str, str],
) -> Tuple[EvidenceTermHitStat, ...]:
    if not query_terms:
        return ()

    field_token_counts = {
        field: Counter(tokenize_for_bm25(text))
        for field, text in field_texts.items() if text
    }

    stats: List[EvidenceTermHitStat] = []
    for term in query_terms:
        field_stats = [
            EvidenceFieldHitStat(field=field, count=count)
            for field, token_counts in field_token_counts.items()
            if (count := token_counts.get(term, 0)) > 0
        ]
        if field_stats:
            stats.append(EvidenceTermHitStat(
                term=term,
                total_count=sum(fs.count for fs in field_stats),
                field_stats=tuple(field_stats),
            ))

    return tuple(stats)


def _build_matched_reason_text(
    evidence_type: str,
    term_hit_stats: Tuple[EvidenceTermHitStat, ...],
) -> str:
    if term_hit_stats:
        fields = list(dict.fromkeys(
            field_stat.field
            for term_stat in term_hit_stats
            for field_stat in term_stat.field_stats
        ))
        if fields:
            return "Matched BM25 query terms in " + ", ".join(fields) + "."

    return (
        "Ranked by fielded BM25 over web search title, domain, and snippet."
        if evidence_type == "web_search_result"
        else "Ranked by fielded BM25 over document title, heading, and body."
    )


def _make_excerpt(text: str, *, query_terms: Tuple[str, ...] = ()) -> str:
    clean = " ".join(text.split())
    if len(clean) <= EXCERPT_MAX_CHARS:
        return clean

    hit_index: Optional[int] = None
    if query_terms:
        text_lower = clean.lower()
        hits = [idx for t in query_terms if t and (idx := text_lower.find(t.lower())) >= 0]
        if hits:
            hit_index = min(hits)

    if hit_index is None:
        return clean[:EXCERPT_MAX_CHARS] + "..."

    half_window = EXCERPT_MAX_CHARS // 2
    start = max(0, hit_index - half_window)
    end = min(len(clean), start + EXCERPT_MAX_CHARS)
    start = max(0, end - EXCERPT_MAX_CHARS)

    excerpt = clean[start:end]
    if start > 0:
        excerpt = "..." + excerpt.lstrip()
    if end < len(clean):
        excerpt = excerpt.rstrip() + "..."
    return excerpt


def _build_context_preview(
    *,
    candidate: EvidenceCandidate,
    content_store: ToolContentStore,
    session_id: str,
    before: int,
    after: int,
    max_chars: int,
) -> Dict[str, object]:
    if candidate.chunk_index < 0 or before <= 0 and after <= 0:
        return {}

    window = content_store.read_chunk_window_by_index(
        content_id=candidate.content_id,
        session_id=session_id,
        chunk_index=candidate.chunk_index,
        before_chunks=max(0, before),
        after_chunks=max(0, after),
    )
    if window is None:
        return {}

    text = window.text
    truncated = False
    if len(text) > max_chars:
        text = text[:max_chars].rstrip()
        truncated = True

    return {
        "before": before,
        "after": after,
        "current_chunk_index": candidate.chunk_index,
        "start_chunk_index": window.metadata.get("start_chunk_index"),
        "end_chunk_index": window.metadata.get("end_chunk_index"),
        "text": text,
        "truncated": truncated or window.truncated,
    }


def _extract_heading_path(chunk: Optional[ContentChunk], stored: StoredContent) -> Tuple[str, ...]:
    raw = None
    if chunk is not None:
        raw = chunk.metadata.get("heading_path") or chunk.metadata.get("headings") or chunk.metadata.get("heading")
    if raw is None:
        raw = stored.metadata.get("heading_path") or stored.metadata.get("headings") or stored.metadata.get("heading")

    if isinstance(raw, (tuple, list)):
        return tuple(item for item in raw if isinstance(item, str) and item)
    if isinstance(raw, str) and raw:
        return (raw,)
    return ()
