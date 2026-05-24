# Output Templates

Use the user's requested language. If no language is specified, match the user's prompt.

## Full report

```markdown
# Reference Review Report

## 1. Discipline Identification

- Primary discipline:
- Secondary discipline:
- Article type:
- Confidence:
- Evidence:
- Review scope and limitations:

## 2. Expected Citation Style

- Required style, if specified:
- Recommended style:
- Acceptable alternatives:
- Reason:

## 3. Detected Citation Style

- In-text citation style:
- Reference-list style:
- Overall consistency:
- Evidence:

## 4. Format Problems

| Location | Current form | Problem | Suggested correction |
|---|---|---|---|

## 5. In-text Citation and Reference List Matching

| Citation / Reference | Issue | Suggested action |
|---|---|---|

## 6. Reference Relevance Review

| Reference | Relevance level | Reason | Suggested action |
|---|---|---|---|

## 7. Priority Revision Suggestions

### Must fix

1. ...

### Suggested fixes

1. ...

## Items Needing Manual Verification

| Item | Why verification is needed |
|---|---|
```

## Short report

```markdown
## Overall Judgment

## Main Citation Style Issues

## Main Reference-Content Issues

## Priority Fixes
```

## Correction-only answer

```markdown
| Original | Problem | Safer corrected form | Verification needed |
|---|---|---|---|
```

## Tone

- Write as teaching-oriented diagnostic feedback.
- Be concrete and evidence-based.
- Avoid overstating certainty when the source metadata or relevance evidence is incomplete.
- Explain why each issue matters for academic writing, source traceability, or disciplinary expectation.
