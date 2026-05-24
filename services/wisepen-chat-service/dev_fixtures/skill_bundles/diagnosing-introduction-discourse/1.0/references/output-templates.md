# Output Templates

Use this template to keep Introduction diagnoses focused on revision advice only.

The diagnosis workflow may produce internal sentence-level move-step annotations, discipline-sensitive step coverage, gap or niche diagnosis, gap-aim alignment, and literature-organization checks. Do not include those internal diagnostic sections in the final response unless the user explicitly asks for them.

## Final Output

Output only the content that would normally appear under `Revision Priorities`. Do not include a diagnostic report title, section number, overall diagnosis, sentence-level annotation, step coverage, gap diagnosis, gap-aim alignment table, literature-organization table, or closing note.

Use this exact structure:

```markdown
### 必须修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Introduction when relevant]
   - 具体改进方向: [specific revision direction in English]

### 建议修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Introduction when relevant]
   - 具体改进方向: [specific revision direction in English]
```

If one block has no items, keep the heading and write `No items.` under it.

## Item Rules

- Put missing conventional functions and serious weak items that harm Introduction logic, gap construction, gap-aim fit, or discipline-expected positioning under `必须修改`.
- Put weaker items that would improve specificity, synthesis, citation support, or rhetorical clarity but do not break the Introduction under `建议修改`.
- Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.
- Start each item with `Issue N:` followed by a concise English issue summary.
- Anchor each item in original wording.
- Do not invent literature, aims, methods, data, findings, or contribution claims for the student.
- Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.
