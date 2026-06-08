from __future__ import annotations

from chat.application.tools.language.services.translation.models import TranslationAssistResult


def format_translation_result(result: TranslationAssistResult) -> str:
    lines = [
        "[Tool Result] translation_assist",
        "",
        f"Source language: {result.source_language}",
        f"Target language: {result.target_language}",
        f"Backend: {result.backend}",
        "",
        "Segments:",
    ]

    for segment in result.segments:
        lines.extend(
            [
                f"[{segment.index}]",
                f"source: {segment.source}",
                f"baseline_translation: {segment.baseline_translation}",
                "",
            ]
        )

    lines.append("Warnings:")
    if result.warnings:
        lines.extend(f"- {warning}" for warning in result.warnings)
    else:
        lines.append("- none")

    lines.extend(
        [
            "",
            "Assistant instructions:",
            "- Treat baseline_translation as machine-translation evidence, not the final answer.",
            "- Produce the final translation yourself using the full conversation context.",
            "- Preserve the user's intended meaning, tone, domain terms, names, numbers, punctuation, and formatting.",
            "- Correct unnatural wording, mistranslated terms, broken references, and segment-boundary artifacts.",
            "- If the user supplied domain term requirements in the conversation, apply them in the final answer.",
            "- If this tool fails or the requested language pair is unsupported, continue translating directly using the assistant's own language ability.",
        ]
    )

    return "\n".join(lines).strip()


def format_translation_error(reason: str) -> str:
    return "\n".join(
        [
            f"[Tool Error] translation_assist failed: {reason}",
            "",
            "Assistant fallback instructions:",
            "- The translation assistance tool failed, but the assistant can still translate using its own language ability.",
            "- Do not tell the user that translation cannot be done solely because this tool failed.",
            "- Continue to translate the user's text directly.",
            "- Preserve the requested language, tone, formatting, names, numbers, and any domain term requirements from the conversation.",
            "- If the tool failed because of an unsupported language pair, translate directly without using the tool.",
        ]
    )
