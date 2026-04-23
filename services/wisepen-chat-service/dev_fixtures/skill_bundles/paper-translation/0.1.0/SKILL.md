---
skill_id: paper-translation
display_name: Paper Translation
description: Translate academic paper fragments (English ↔ Simplified Chinese) while preserving formulas, citations, terminology, figures, tables, and references.
version: 0.1.0
enabled: true
triggers:
  - 翻译
  - 论文翻译
  - 学术翻译
  - paper translation
  - translate paper
  - translate this paper
  - translate the paper
  - academic translation
  - 帮我翻译这篇论文
  - 帮我翻译这篇
  - 翻译这段论文
assets:
  - path: references/citation-styles.md
    kind: reference
    description: Normalization rules for common citation styles (APA/MLA/Chicago/IEEE) when rendering citations in translation.
  - path: templates/translation-header-template.md
    kind: template
    description: Header template to prepend at the top of every translation output for traceability.
---

# Paper Translation Skill

You are handling a **paper translation** request. Follow these rules strictly and in order.

## 1. Direction & Language
- Detect source language from the paper fragment. Default output is the opposite language (English ↔ Simplified Chinese).
- If the user explicitly specifies a target language (e.g. "translate into English"), obey it.

## 2. Output Shape
- Always start with the header defined in `templates/translation-header-template.md` (load via `load_skill_asset` only if you haven't seen it in this turn).
- Then provide the translation. Do **not** paraphrase, summarize, or shorten; this is translation, not rewriting.
- If the input is clearly incomplete (e.g. cut off mid-sentence), translate what you have and add a short `[Note: input appears truncated]` line at the end.

## 3. Formulas
- Preserve all math verbatim. Inline formulas keep `$...$`; display formulas keep `$$...$$` or `\\[...\\]` as in the source.
- Do **not** translate variable names, function names, or operator symbols inside formulas.
- Do **not** re-derive or re-format formulas.

## 4. Citations
- Keep in-text citations syntactically intact. Examples: `[12]`, `[Smith et al., 2020]`, `(Li & Wang 2019)`.
- Do **not** translate author names.
- If the user asks about citation style normalization, consult `references/citation-styles.md` via `load_skill_asset`.

## 5. Terminology
- Use standard academic terminology in the target language. When a term has a well-established Chinese translation, use it (e.g. "optimizer" → "优化器", "transformer" → "Transformer（保留英文）").
- On first mention, you MAY give `中文译法 (English original)` when the English term is a proper noun, model name, or architecture name.
- Keep model names, dataset names, and benchmark names in their original form (e.g. "BERT", "ImageNet", "GLUE").

## 6. Section Titles, Figures, Tables
- Translate section titles while keeping numbering: `3.2 Method` → `3.2 方法`.
- Figure and table captions: translate the caption text; keep `Figure 3`/`Table 2` or translate to `图 3`/`表 2` consistently with the rest of the output.
- Do **not** invent or re-caption figures that are not in the source.

## 7. References Section
- When translating a "References" block, **keep each entry in its original form**. Do not translate author names, venue names, or paper titles unless the user explicitly asks.
- If asked to normalize the style (APA / MLA / Chicago / IEEE), call `load_skill_asset` on `references/citation-styles.md` first and follow the rules there.

## 8. What NOT to do
- Do not add commentary on paper quality, novelty, or correctness.
- Do not hallucinate missing sections, missing citations, or missing figures.
- Do not ask the user clarification questions if the input is unambiguous; just translate.

---

When finished, you may ask a single short follow-up like "Need any terminology adjustments?" — only if it adds value.
