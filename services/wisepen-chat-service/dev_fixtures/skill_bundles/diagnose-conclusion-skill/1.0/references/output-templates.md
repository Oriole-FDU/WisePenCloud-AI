# Conclusion Output Templates

Use this template to keep Conclusion diagnoses focused on revision advice only.

The diagnosis workflow may produce internal annotations, coverage judgments, and logic-chain checks. Do not include those internal diagnostic sections in the final response.

## Final Output

Output only the content that would normally appear under `Revision Priorities`. Do not include a `Revision Priorities` heading, section number, diagnosis summary, profile table, sentence-level annotation, coverage table, logic diagnosis, or closing note.

Use this exact structure:

```markdown
### 必须修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Conclusion when relevant]
   - 具体改进方向: [specific revision direction in English]

### 建议修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Conclusion when relevant]
   - 具体改进方向: [specific revision direction in English]
```

If one block has no items, keep the heading and write `No items.` under it.

## Item Rules

- Put missing expected steps and weak items that materially affect Conclusion synthesis, significance, credibility, implication, future direction, or closure under `必须修改`.
- Put weak items that would improve specificity, evidence, connection, implication, or flow but do not break the core Conclusion logic under `建议修改`.
- Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.
- Start each item with `Issue N:` followed by a concise English issue summary.
- Anchor each item in original wording.
- Do not invent findings, limitations, implications, applications, future directions, or contributions for the student.
- Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.
