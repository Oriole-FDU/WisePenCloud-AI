"""内置通用来源的投影规则；应用编排只消费注册的回调。"""

from scholar.domain.models.identifiers import parse_identifier


def s2_signals(metadata: dict) -> bool:
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
    # 只有本地论文基础信息不算富化；离线同步的 S2 信号才可终止通用链。
    return s2_signals(metadata)


def s2_aliases(metadata: dict) -> list:
    values = metadata.get("s2", {})
    aliases = []
    if values.get("paper_id"):
        aliases.append(parse_identifier(f"s2:{values['paper_id']}"))
    if values.get("corpus_id") is not None:
        aliases.append(parse_identifier(f"corpusid:{values['corpus_id']}"))
    return aliases


def openalex_aliases(metadata: dict) -> list:
    value = metadata.get("openalex", {}).get("id")
    return [] if value is None else [parse_identifier(value)]


def openalex_signals(metadata: dict) -> bool:
    values = metadata.get("openalex", {})
    return any(
        values.get(name) is not None
        for name in (
            "cited_by_count",
            "fwci",
            "citation_normalized_percentile",
            "primary_topic",
            "open_access",
            "is_retracted",
        )
    ) or bool(
        values.get("authorships") or values.get("mesh") or values.get("study_designs")
    )


def s2_short_lived(record) -> bool:
    if record.enrichment is None:
        return True
    values = record.enrichment.metadata.get("s2", {})
    tldr = values.get("tldr")
    return (
        (record.details_confirmed and record.enrichment.paper is None)
        or values.get("citation_count") is None
        or values.get("influential_citation_count") is None
        or not tldr
        or not tldr.get("text")
    )
