from __future__ import annotations

from chat.application.tools.web.services.web_crawl.enums import CrawlItemKind
from chat.application.tools.web.services.web_crawl.models import CrawlResult


def format_crawl_result(result: CrawlResult) -> str:
    """将爬取结果格式化为结构化文本，供 LLM 读取。"""
    lines = [
        "[Tool Result] web_crawl",
        "",
        "Objective:",
        result.objective,
        "",
        "Summary:",
        f"- seed_urls: {len(result.seed_urls)}",
        f"- fetched_pages: {result.fetched_pages}",
        f"- documents_found: {result.documents_found}",
        f"- skipped_urls: {result.skipped_count}",
        f"- max_depth: {result.max_depth}",
        f"- max_pages: {result.max_pages}",
        f"- crawl_budget_exhausted: {str(result.crawl_budget_exhausted).lower()}",
        "",
    ]

    page_items = [
        item for item in result.items if item.kind == CrawlItemKind.PAGE.value
    ]
    document_items = [
        item for item in result.items if item.kind == CrawlItemKind.DOCUMENT.value
    ]
    skipped_items = [
        item
        for item in result.items
        if item.kind in {CrawlItemKind.SKIPPED.value, CrawlItemKind.ERROR.value}
    ]

    if page_items:
        lines.append("Pages:")
        for index, item in enumerate(page_items, 1):
            lines.append(f"{index}. URL: {item.url}")
            lines.append(f"   depth: {item.depth}")
            if item.source_url:
                lines.append(f"   source_url: {item.source_url}")
            lines.append("   content:")
            lines.append(f"   {item.content_block or ''}")
            lines.append("")

    if document_items:
        lines.append("Document parse required:")
        for item in document_items:
            if item.file_ref:
                lines.append(f"- {item.file_ref}")
        lines.append("")

    if skipped_items:
        lines.append("Skipped:")
        for item in skipped_items[:20]:
            lines.append(f"- url: {item.url}")
            lines.append(f"  reason: {item.skip_reason or 'unknown'}")
            if item.error:
                lines.append(f"  error: {item.error}")
        lines.append("")

    lines.extend(
        [
            "Assistant instructions:",
            "- Treat seed page results as primary evidence and recursive pages as supplemental evidence.",
            "- Use evidence_rank on returned content_ids before answering complex questions.",
            '- Call document_parse once with all file_refs listed under "Document parse required".',
            "- Do not call document_parse one file at a time.",
            "- Do not call web_crawl again unless crawl_budget_exhausted=true and the user request still requires more linked pages.",
            "- Do not infer skipped, blocked, failed, or unfetched content.",
        ]
    )

    return "\n".join(lines)