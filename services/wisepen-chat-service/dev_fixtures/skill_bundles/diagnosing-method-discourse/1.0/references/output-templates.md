# Output Templates

Use this template to keep Methods diagnoses focused on revision advice only.

The diagnosis workflow may produce internal move-step annotations, required-step coverage, unjustified-decision checks, and method-logic checks. Do not include those internal diagnostic sections in the final response unless the user explicitly asks for them.

## Final Output

Output only the content that would normally appear under `Revision Priorities`. Do not include a diagnostic report title, section number, overall diagnosis, move-step annotation, required-step coverage, unjustified-methodological-decision table, method-logic table, or closing note.

Use this exact structure:

```markdown
### 必须修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English]
   - 具体改进方向: [specific revision direction in English]

### 建议修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English]
   - 具体改进方向: [specific revision direction in English]
```

If one block has no items, keep the heading and write `No items.` under it.

## Item Rules

- Put missing items and weak items that affect rigour, credibility, reproducibility, validity, or method-chain logic under `必须修改`.
- Before listing a `必须修改` item, re-check the original sentence to confirm the information is truly absent or too weak to support the method chain.
- Put weak items that mainly improve transparency, completeness, or reader trust under `建议修改`.
- Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.
- Start each item with `Issue N:` followed by a concise English issue summary.
- Anchor each item in original wording.
- Do not write priorities that require inventing information not present in the student's study.
- Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.
