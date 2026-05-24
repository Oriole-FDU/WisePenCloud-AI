# Output Templates

Use these templates to format diagnostic output. Adapt length to the user's request.

## Full Diagnostic Report

```markdown
## 1. Overall Diagnosis

[Brief paragraph identifying research type, discipline if known, main strengths, and main credibility problems.]

## 2. Move-Step Annotation

| ID | Text span | Primary label | Secondary label(s) | Confidence | Reason |
|---|---|---|---|---|---|
| S1 | ... | M1-S3 | ... | High | ... |

## 3. Required Step Coverage

| Step | Status | Evidence | Problem | Why it matters |
|---|---|---|---|---|
| M1-S3 | Present/Weak/Missing/Not Applicable/Unclear | ... | ... | ... |

## 4. Unjustified Methodological Decisions

| Sentence | Decision type | Why justification is needed | Missing rationale | Suggested direction |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

## 5. Method Logic Issues

| Issue type | Evidence | Problem | Affected step(s) | Revision direction |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

## 6. Revision Priorities

### 必须补充

| 原文句子 | 问题 | 具体改进方向 |
|---|---|---|
| ... | ... | ... |

### 建议补充

| 原文句子 | 问题 | 具体改进方向 |
|---|---|---|
| ... | ... | ... |
```

## Short Classroom Feedback

```markdown
## Diagnosis

[2-4 sentences on what the Methods section already does and what weakens rigour or credibility.]

## Most Important Gaps

### 必须补充

| 原文句子 | 问题 | 具体改进方向 |
|---|---|---|
| ... | ... | ... |

### 建议补充

| 原文句子 | 问题 | 具体改进方向 |
|---|---|---|
| ... | ... | ... |
```

## Sentence-Level Comment Template

Use when the user wants feedback attached to specific sentences.

```markdown
| Sentence | DRaC label | Problem | Teaching feedback |
|---|---|---|---|
| ... | M2-S4 / M3-S2 | ... | ... |
```

## Coverage-Only Template

Use when the user asks only which moves or steps are present/missing.

```markdown
| Step | Status | Evidence | Note |
|---|---|---|---|
| M1-S3 | ... | ... | ... |
```

## Revision Priority Rules

End with two blocks in this order: 必须补充, then 建议补充.

Use `原文句子 | 问题 | 具体改进方向` in both blocks. The 原文句子 column must quote the corresponding English sentence or clause from the student text.

- Put Missing items and Weak items that affect rigour, credibility, reproducibility, validity, or method-chain logic in 必须补充.
- Before listing a 必须补充 item, re-check the original sentence to confirm the information is truly absent or too weak to support the method chain.
- Put Weak items that mainly improve transparency, completeness, or reader trust in 建议补充.
- Treat details as 不必补充 when they are unrelated to the study design or already sufficiently covered; do not output a separate 不必补充 table.
- Keep the feedback evidence-based and do not invent details that are not supported by the student's study.

Do not write priorities that require inventing information not present in the student's study.

