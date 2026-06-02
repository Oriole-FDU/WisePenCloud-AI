# Output Templates

Use this template to keep reference-use reviews focused on concrete revision advice only.

The review workflow may produce internal citation-style identification, format checks, in-text/reference matching checks, relevance judgments, and verification notes. Do not include those internal diagnostic sections in the final response unless the user explicitly asks for them.

## Final Output

Output only the content that would normally appear under priority revision suggestions. Do not include a report title, section number, discipline identification, expected style, detected style, format-problem table, matching table, relevance-review table, manual-verification table, diagnostic summary, or closing note.

Use this exact structure:

```markdown
### 必须修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original citation, reference entry, sentence, or text span]
   - 问题: [diagnosis in English]
   - 具体改进方向: [specific revision direction in English]

### 建议修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original citation, reference entry, sentence, or text span]
   - 问题: [diagnosis in English]
   - 具体改进方向: [specific revision direction in English]
```

If one block has no items, keep the heading and write `No items.` under it.

## Item Rules

- Put issues that clearly break the required or expected citation style, traceability, in-text/reference correspondence, or source relevance under `必须修改`.
- Put issues that improve consistency, completeness, style fit, or source integration but do not break traceability under `建议修改`.
- Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.
- Start each item with `Issue N:` followed by a concise English issue summary.
- Use `原文句子` for the original citation, reference entry, sentence, or relevant text span.
- Use `问题` for the diagnosis. Include missing metadata, style inconsistency, unmatched citation/reference, weak source relevance, or uncertainty as needed.
- Use `具体改进方向` for the safe correction direction. Do not invent missing authors, titles, years, journal names, publishers, DOIs, URLs, page ranges, or abstracts.
- Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.
