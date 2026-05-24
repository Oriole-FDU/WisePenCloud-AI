# Conclusion Output Templates

Use this template to keep Conclusion diagnoses focused on revision advice only.

The diagnosis workflow may produce internal annotations, coverage judgments, and logic-chain checks. Do not include those internal diagnostic sections in the final response.

## Final Output

Output only the content that would normally appear under `Revision Priorities`. Do not include a `Revision Priorities` heading, section number, diagnosis summary, profile table, sentence-level annotation, coverage table, logic diagnosis, or closing note.

```markdown
### 必须修改

| 原文句子 | 问题 | 为什么影响 Conclusion | 具体改进方向 |
|---|---|---|---|
| ... | ... | ... | ... |

### 建议修改

| 原文句子 | 问题 | 为什么影响 Conclusion | 具体改进方向 |
|---|---|---|---|
| ... | ... | ... | ... |
```

## Item Rules

- Put missing expected steps and weak items that materially affect Conclusion synthesis, significance, credibility, implication, future direction, or closure under `必须修改`.
- Put weak items that would improve specificity, evidence, connection, implication, or flow but do not break the core Conclusion logic under `建议修改`.
- Anchor each item in original wording.
- Do not invent findings, limitations, implications, applications, future directions, or contributions for the student.
- If one block has no items, still keep the block and write `暂无` in the table cells.
