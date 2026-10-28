"""内置通用来源的投影规则与回调函数。

编排层（LookupRunner）通过注册的回调消费各来源能力，不感知上游字段结构。
本模块定义三类回调：

- ``*_signals``：判断元数据是否已含有效富化事实；命中即短路通用富化链
  （Arxiv → S2 → OpenAlex），避免无谓的网络与配额开销。
- ``*_aliases``：提取各平台唯一标识并标准化为 ``PaperIdentifier``，用于
  多 Key 缓存写入、跨源身份接力、串号冲突检测与缓存级联失效。
- ``*_short_lived``：判定记录是否为暂态/残缺数据，以决定短 TTL（负缓存）
  还是长 TTL（正缓存）。
"""

from scholar.domain.models.identifiers import parse_identifier


def s2_signals(metadata: dict) -> bool:
    """判定 S2 元数据是否含有效富化信号。

    三类价值维度取 OR，覆盖不同时效与学科的论文：

    - 计量指标：``citation_count`` / ``influential_citation_count`` /
      ``is_open_access`` 任一非 None（新论文可能尚无 TLDR，但已有引用统计）。
    - 速读价值：``tldr.text`` 非空（引用为 0 的预印本也可能已有 LLM 摘要）。
    - 科研资产：``open_access_pdf`` 或 ``bibtex`` 存在（直接满足阅读/引用刚需）。
    """
    values = metadata.get("s2", {})
    tldr = values.get("tldr")
    return (
        any(
            values.get(key) is not None
            for key in (
                "citation_count",
                "influential_citation_count",
                "is_open_access",
            )
        )
        or bool(tldr and tldr.get("text"))
        or bool(values.get("open_access_pdf") or values.get("bibtex"))
    )


def arxiv_signals(metadata: dict) -> bool:
    """判定本地仓储（arXiv/DOI）是否已含足够富化信号。

    本地基础元信息只算检索底库，不算外部增量；只有离线同步注入过 S2 字段
    才算真正富化，故直接复用 ``s2_signals``。
    """
    return s2_signals(metadata)


def s2_aliases(metadata: dict) -> list:
    """从 S2 响应中提取 paper_id 与 corpus_id 作为别名。

    别名用于：多 Key 共享缓存（同一份数据同时写入 ``s2:<id>``、
    ``corpusid:<id>``）、跨源身份接力，以及比对 known 与 actual 别名以
    拦截模糊匹配造成的串号。
    """
    values = metadata.get("s2", {})
    aliases = []
    if values.get("paper_id"):
        aliases.append(parse_identifier(f"s2:{values['paper_id']}"))
    if values.get("corpus_id") is not None:
        aliases.append(parse_identifier(f"corpusid:{values['corpus_id']}"))
    return aliases


def openalex_aliases(metadata: dict) -> list:
    """从 OpenAlex 响应中提取 Work URI（如 ``https://openalex.org/W...``）。"""
    value = metadata.get("openalex", {}).get("id")
    return [] if value is None else [parse_identifier(value)]


def openalex_signals(metadata: dict) -> bool:
    """判定 OpenAlex 元数据是否含有效富化信号。

    两组 OR：

    - 宏观标量：``cited_by_count`` / ``primary_topic`` /
      ``open_access`` / ``is_retracted`` 任一非 None。
    - 结构化字段：``mesh`` / ``study_designs`` 任一存在（医学专用字段）。
    """
    values = metadata.get("openalex", {})
    return any(
        values.get(name) is not None
        for name in (
            "cited_by_count",
            "primary_topic",
            "open_access",
            "is_retracted",
        )
    ) or bool(values.get("mesh") or values.get("study_designs"))


def s2_short_lived(record) -> bool:
    """判定 S2 lookup 缓存是否暂态或缺少关键信号（是则用短 TTL）。

    与 ``s2_signals`` 的「宽进」互补：这里「严出」，任一残缺即降级为短 TTL，
    避免负结果或半成品长期占缓存。触发条件：

    - 已确认未找到（``paper`` 为空）；
    - ``citation_count`` 或 ``influential_citation_count`` 尚未出炉；
    - ``tldr`` 缺失或其 ``text`` 为空（LLM 摘要仍在生成）。
    """
    if record.paper is None:
        return True
    values = record.paper.metadata.get("s2", {})
    tldr = values.get("tldr")
    return (
        values.get("citation_count") is None
        or values.get("influential_citation_count") is None
        or not tldr
        or not tldr.get("text")
    )
