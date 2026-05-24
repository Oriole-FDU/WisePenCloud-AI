# Output Templates

Use these templates to format diagnostic output. Adapt length to the user's request.

## Full Diagnostic Report

```markdown
## 1. Overall Diagnosis

[Brief paragraph identifying discipline, article type if known, what the Introduction already does, and the main rhetorical problems.]

## 2. Sentence-Level Move-Step Annotation

| ID | Text span | Primary label | Secondary label(s) | Confidence | Reason |
|---|---|---|---|---|---|
| S1 | ... | M1S2 | ... | High | ... |

## 3. Discipline-Sensitive Step Coverage

| Conventional step or function | Status | Evidence | Problem | Why it matters |
|---|---|---|---|---|
| M1S2 Making topic generalizations | Present/Weak/Missing/Not Applicable/Unclear | ... | ... | ... |

## 4. Gap or Niche Diagnosis

| Gap/niche evidence | Gap type | Evaluation | Revision direction |
|---|---|---|---|
| ... | ... | ... | ... |

## 5. Gap-Aim Alignment

| Gap sentence | Aim/contribution sentence | Alignment problem | Revision direction |
|---|---|---|---|
| ... | ... | ... | ... |

## 6. Literature Organization

| Evidence | Problem pattern | Why it weakens the Introduction | Revision direction |
|---|---|---|---|
| ... | ... | ... | ... |

## 7. Revision Priorities

### 必须修改

| 原文句子 | 问题 | 为什么影响 Introduction | 具体改进方向 |
|---|---|---|---|
| ... | ... | ... | ... |

### 建议修改

| 原文句子 | 问题 | 为什么影响 Introduction | 具体改进方向 |
|---|---|---|---|
| ... | ... | ... | ... |
```

## Short Classroom Feedback

```markdown
## Diagnosis

[2-4 sentences on what the Introduction already does and what most weakens territory, niche, or present-study positioning.]

## Most Important Revisions

### 必须修改

| 原文句子 | 问题 | 为什么影响 Introduction | 具体改进方向 |
|---|---|---|---|
| ... | ... | ... | ... |

### 建议修改

| 原文句子 | 问题 | 为什么影响 Introduction | 具体改进方向 |
|---|---|---|---|
| ... | ... | ... | ... |
```

## Sentence-Level Comment Template

Use when the user wants feedback attached to specific sentences.

```markdown
| Sentence | Move-step label | Problem | Teaching feedback |
|---|---|---|---|
| ... | M2S4 / M3S10 | ... | ... |
```

## Coverage-Only Template

Use when the user asks only which moves or steps are present or missing.

```markdown
| Conventional step or function | Status | Evidence | Note |
|---|---|---|---|
| M2S4 Indicating a gap | Present/Weak/Missing/Not Applicable/Unclear | ... | ... |
```

## Revision Priority Rules

End with two blocks in this order:

1. 必须修改
2. 建议修改

Use `原文句子 | 问题 | 为什么影响 Introduction | 具体改进方向` in both blocks.

- Put Missing conventional functions and serious Weak items in `必须修改`.
- Put less serious Weak items in `建议修改`.
- Keep feedback evidence-based.
- Do not invent literature, aims, methods, data, findings, or contribution claims.
