from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from chat.application.tools.common.plaintext_ranking import (
    PlainTextDocument,
    PlainTextRankRequest,
    rank_plain_text,
)
from chat.domain.entities import ChatMessage
from chat.domain.interfaces.tool import BaseTool
from chat.domain.repositories import MessageRepository
from common.logger import log_fail

# --- 常量配置 ---
MAX_HISTORY_QUERIES = 4           # 最多接受几个 keyword
DEFAULT_HISTORY_LIMIT = 10       # 默认返回结果数量
RECALL_WINDOW_MULTIPLIER = 4     # 扫描窗口 = max(scan_limit, limit * 倍数)
DEFAULT_HISTORY_SCAN_LIMIT = 200 # 默认扫描最近 N 条消息


@dataclass(frozen=True, slots=True)
class KeywordExactCandidate:
    """keyword exact 召回候选及其内部排序指标。

    Attributes:
        message: 命中的聊天消息。
        case_insensitive_match_count: 大小写不敏感匹配的 keyword 数量。
        case_sensitive_match_count: 大小写敏感匹配的 keyword 数量。
        first_case_insensitive_position: 首次 keyword 命中的字符位置（越小越靠前）。
        original_index: 消息在扫描列表中的原始索引（用于稳定排序）。
    """

    message: ChatMessage
    case_insensitive_match_count: int
    case_sensitive_match_count: int
    first_case_insensitive_position: int
    original_index: int


class SearchHistoricalMessagesTool(BaseTool):
    """历史消息检索工具，按 keywords 召回并按 objective 做纯文本重排。"""

    def __init__(
        self,
        message_repo: MessageRepository,
    ) -> None:
        """初始化历史消息仓储依赖。"""
        self._message_repo = message_repo

    @property
    def name(self) -> str:
        """返回工具名称。"""
        return "search_historical_messages"

    @property
    def description(self) -> str:
        """返回工具说明。"""
        return (
            "Search historical chat messages with exact keyword recall and objective-based ranking. "
            "Use objective for BM25 lexical ranking, and keywords for exact recall. "
            "If relevance scores are tied, newer messages are returned first. "
            "The tool returns complete ranked message results as raw tool output. "
            "When the result is large, the runtime may cache the tool output as ToolContent "
            "and expose content_id, next_offset, and chunk metadata for follow-up reading."
        )

    @property
    def parameters_schema(self) -> Dict[str, Any]:
        """返回工具参数结构。"""
        return {
            "type": "object",
            "properties": {
                "keywords": {
                    "type": "array",
                    "items": {"type": "string", "minLength": 1},
                    "minItems": 1,
                    "maxItems": MAX_HISTORY_QUERIES,
                    "description": (
                        "Exact keyword phrases used for raw substring recall. "
                        "A message matching any keyword enters the exact recall list. "
                        "Raw substring matching is used so identifiers and filenames stay intact."
                    ),
                },
                "objective": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "The final ranking goal used as the BM25 lexical ranking input "
                        "over exact-recalled candidates."
                    ),
                },
                "start_time": {
                    "type": "string",
                    "description": "ISO 8601 start time for filtering messages.",
                },
                "end_time": {
                    "type": "string",
                    "description": "ISO 8601 end time for filtering messages.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of ranked messages to return. Defaults to 10.",
                    "default": DEFAULT_HISTORY_LIMIT,
                },
                "scan_limit": {
                    "type": "integer",
                    "description": (
                        "Maximum recent messages to scan before keyword exact filtering. "
                        "Defaults to 200."
                    ),
                    "default": DEFAULT_HISTORY_SCAN_LIMIT,
                },
            },
            "required": ["objective", "keywords"],
            "additionalProperties": False,
        }

    async def execute(self, context: Dict[str, Any], **kwargs) -> str:
        """执行历史消息的 keyword 召回与 objective 重排。

        流程：归一化入参 → 扫描历史消息 → keyword exact 召回 → 多指标排序 →
        objective BM25 重排 → 格式化输出。

        Args:
            context: 执行上下文，需包含 session_id。
            **kwargs: 工具参数，需包含 keywords 和 objective，可选 start_time、
                      end_time、limit、scan_limit。

        Returns:
            格式化后的历史消息文本；长结果由统一工具输出切面缓存为 ToolContent window。
        """
        # --- 1. 校验会话 ---
        session_id: Optional[str] = context.get("session_id")
        if not session_id:
            return "[Tool Error] Missing session_id in execution context."

        # --- 2. 归一化 keyword 列表 ---
        keywords = _normalize_plain_terms(kwargs["keywords"], limit=MAX_HISTORY_QUERIES)
        if not keywords:
            return "[Tool Error] keywords list contains no valid exact search terms after normalization."

        # --- 3. 归一化 objective，作为 BM25 排序的 query ---
        objective = " ".join(kwargs["objective"].strip().split())
        if not objective:
            return "[Tool Error] objective contains no valid ranking text after normalization."
        ranking_queries = [objective]

        # --- 4. 解析可选的时间范围过滤 ---
        start_time_or_error = _parse_optional_time(kwargs.get("start_time"), "start_time")
        if isinstance(start_time_or_error, str):
            return start_time_or_error
        start_time = start_time_or_error

        end_time_or_error = _parse_optional_time(kwargs.get("end_time"), "end_time")
        if isinstance(end_time_or_error, str):
            return end_time_or_error
        end_time = end_time_or_error

        limit = kwargs.get("limit", DEFAULT_HISTORY_LIMIT)
        scan_limit = kwargs.get("scan_limit", DEFAULT_HISTORY_SCAN_LIMIT)
        # 确保扫描窗口至少能覆盖 limit 的倍数，给 keyword 召回留足空间
        scan_limit = max(scan_limit, limit * RECALL_WINDOW_MULTIPLIER)

        # --- 5. 从仓储扫描历史消息 ---
        try:
            scanned_messages = await self._message_repo.get_recent_by_session(
                session_id=session_id,
                start_time=start_time,
                end_time=end_time,
                limit=scan_limit,
            )
        except Exception as e:
            log_fail("历史消息全文检索", e, session=session_id, keywords=keywords)
            return f"[Tool Error] Search failed: {e}"

        # --- 6. Keyword exact 召回：raw substring 匹配，构造候选集 ---
        exact_candidates = [
            _build_keyword_exact_candidate(message=message, keywords=keywords, index=index)
            for index, message in enumerate(scanned_messages)
        ]
        exact_candidates = [candidate for candidate in exact_candidates if candidate is not None]

        # 候选内部排序：匹配数降序 → 首次命中位置升序 → 大小写敏感匹配数降序 → 原始索引升序
        exact_candidates.sort(
            key=lambda item: (
                -item.case_insensitive_match_count,
                item.first_case_insensitive_position,
                -item.case_sensitive_match_count,
                item.original_index,
            )
        )

        if not exact_candidates:
            return (
                "[Tool Result] No historical messages found for keywords: "
                + ", ".join(keywords)
                + "."
            )

        keyword_exact_ranked_ids = [
            str(candidate.message.id)
            for candidate in exact_candidates
        ]
        candidates = [candidate.message for candidate in exact_candidates]

        # --- 7. Objective BM25 重排：对 keyword 候选集做纯文本语义排序 ---
        documents = [
            PlainTextDocument(
                document_id=str(candidate.message.id),
                text=_build_rank_text(candidate.message),
                original_rank=candidate.original_index,
            )
            for candidate in exact_candidates
        ]
        ranked = rank_plain_text(
            PlainTextRankRequest(
                queries=ranking_queries,
                documents=documents,
                top_k=limit,
                keyword_exact_ranked_ids=keyword_exact_ranked_ids,
                keyword_exact_queries=keywords,
                include_original_rank=True,
            )
        )
        messages_by_id = {str(message.id): message for message in candidates}
        selected = [
            messages_by_id[item.document_id]
            for item in ranked
            if item.document_id in messages_by_id
        ]

        # --- 8. 格式化输出：附加检索元信息 + 角色 + 时间戳 + 内容 ---
        return _format_history_search_result(
            objective=objective,
            keywords=keywords,
            ranking_queries=ranking_queries,
            selected=selected,
            candidate_count=len(candidates),
            scanned_message_count=len(scanned_messages),
            start_time=start_time,
            end_time=end_time,
        )


def _format_history_search_result(
    *,
    objective: str,
    keywords: List[str],
    ranking_queries: List[str],
    selected: List[ChatMessage],
    candidate_count: int,
    scanned_message_count: int,
    start_time: Optional[datetime],
    end_time: Optional[datetime],
) -> str:
    """格式化历史消息检索结果。

    Args:
        objective: 排序目标。
        keywords: exact 召回关键词。
        ranking_queries: 排序 query 列表。
        selected: 最终选中的消息列表。
        candidate_count: keyword exact 召回候选数量。
        scanned_message_count: 扫描消息数量。
        start_time: 可选起始时间。
        end_time: 可选结束时间。

    Returns:
        完整历史消息检索结果文本。
    """
    lines = [
        "[Tool Result] Historical message search results",
        f"Objective: {objective}",
        f"Keywords: {', '.join(keywords)}",
        f"Ranking queries: {', '.join(ranking_queries)}",
        f"Result count: {len(selected)}",
        f"Candidate count: {candidate_count}",
        f"Scanned message count: {scanned_message_count}",
        "Freshness tiebreak: created_at_desc",
        "Keyword exact match: raw_substring_case_insensitive_or",
    ]

    if start_time:
        lines.append(f"Start time: {start_time.isoformat()}")
    if end_time:
        lines.append(f"End time: {end_time.isoformat()}")

    lines.extend(["", "[Ranked Historical Messages]"])

    if not selected:
        lines.append("(none)")
        return "\n".join(lines)

    lines.extend(
        f"[{message.role.value}] ({message.created_at.isoformat()}): {message.content or ''}"
        for message in selected
    )
    return "\n".join(lines)


def _normalize_plain_terms(values: List[str], *, limit: int) -> List[str]:
    """对 keyword 列表做最小文本清洗：去空白、压缩空格、去重、截断。

    Args:
        values: 原始 keyword 列表。
        limit: 最多保留前 N 个有效 keyword。

    Returns:
        归一化后的 keyword 列表。
    """
    terms: List[str] = []
    seen = set()
    for value in values:
        normalized = " ".join(value.strip().split())
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        terms.append(normalized)
        if len(terms) >= limit:
            break
    return terms


def _parse_optional_time(value: Optional[str], field_name: str) -> Optional[datetime] | str:
    """解析可选的 ISO 8601 时间参数字符串。

    Args:
        value: ISO 8601 时间字符串，None 或空串表示不限制。
        field_name: 字段名称，用于错误提示。

    Returns:
        解析成功返回 datetime；值为空返回 None；
        格式错误返回错误提示字符串（调用方通过 isinstance 区分）。
    """
    if not value:
        return None

    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return f"[Tool Error] Invalid {field_name} format. Expected ISO 8601 datetime string."


def _build_keyword_exact_candidate(
    *,
    message: ChatMessage,
    keywords: List[str],
    index: int,
) -> Optional[KeywordExactCandidate]:
    """对单条消息执行 keyword exact 召回，构造候选对象。

    匹配规则：raw substring + casefold 大小写不敏感（OR 逻辑）。
    任一 keyword 命中 case_insensitive 即可进入候选集。
    同时记录 case_sensitive 命中数，用于候选内部排序。

    Args:
        message: 待检查的聊天消息。
        keywords: 归一化后的 keyword 列表。
        index: 消息在扫描列表中的原始索引。

    Returns:
        命中任一 keyword 时返回 KeywordExactCandidate，否则返回 None。
    """
    text = _build_rank_text(message)
    lowered_text = text.casefold()
    case_insensitive_match_count = 0
    case_sensitive_match_count = 0
    first_position: Optional[int] = None

    for keyword in keywords:
        lowered_keyword = keyword.casefold()
        position = lowered_text.find(lowered_keyword)

        if position >= 0:
            case_insensitive_match_count += 1
            if first_position is None or position < first_position:
                first_position = position

        if keyword in text:
            case_sensitive_match_count += 1

    if case_insensitive_match_count <= 0 or first_position is None:
        return None

    return KeywordExactCandidate(
        message=message,
        case_insensitive_match_count=case_insensitive_match_count,
        case_sensitive_match_count=case_sensitive_match_count,
        first_case_insensitive_position=first_position,
        original_index=index,
    )


def _build_rank_text(message: ChatMessage) -> str:
    """构造用于纯文本排序的拼接文本。

    按 角色 + 正文 + 推理内容 的顺序拼接，每部分一行。

    Args:
        message: 聊天消息。

    Returns:
        拼接后的纯文本字符串。
    """
    parts = [message.role.value]
    if message.content:
        parts.append(message.content)
    if message.reasoning_content:
        parts.append(message.reasoning_content)
    return "\n".join(parts)