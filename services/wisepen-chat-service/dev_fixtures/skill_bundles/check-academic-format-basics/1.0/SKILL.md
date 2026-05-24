---
skill_id: check-academic-format-basics
display_name: Check Academic Format Basics
name: check-academic-format-basics
description: Pre-checks student academic papers for only the basic format, grammar, pronoun clarity, academic tone, rhetorical-question opening, and visible formatting issues listed in this skill before section-specific diagnosis. Use when Codex needs to identify only revision-needed items involving academic heading capitalization, obvious grammar errors, unclear pronoun reference, overclaiming or promotional academic tone, rhetorical questions at paragraph or section openings, and visible basic academic formatting problems; do not use it to check citation quality, reference-list accuracy, source relevance, or reference-use problems.
version: 1.0
enabled: true
triggers:
  - 学术格式检查
  - academic format check
  - academic formatting
  - grammar and tone check
  - 论文基础格式检查
  - 检查论文格式
  - 检查语法和学术语气
---



# Check Academic Format Basics

Use this skill as a preliminary check before more specific Introduction, Methods, Discussion, Conclusion, or reference-use diagnosis.

Produce only items that need revision. Do not provide an overall evaluation, score, praise, summary, or full rewrite.

## Scope

Check for:

1. basic academic heading format;
2. obvious grammar problems;
3. unclear pronoun reference;
4. overclaiming, exaggerated, or promotional academic tone;
5. rhetorical questions at paragraph or section openings;
6. visible basic academic formatting problems.

Only flag the issue types explicitly listed in this skill. If a problem is outside these categories, do not include it.

Do not replace section-specific discourse diagnosis, citation checking, reference-use review, or detailed proofreading. Only flag issues that are visible from the submitted text and fall within this skill's scope.

Do not flag citation or reference-use problems, including missing citations, inaccurate citation style, reference-list completeness, in-text/reference mismatch, source relevance, DOI/URL format, or bibliography-entry formatting. The word `References` may be checked only as a section heading capitalization or heading-style issue.

## Severity Labels

Use only these labels to decide which output part an item belongs to:

- `必须修改`: the issue is clearly incorrect, violates the user's stated format rule, or seriously damages sentence correctness.
- `建议修改`: the issue is understandable but weakens academic clarity, formality, restraint, or consistency.

Do not repeat `必须修改` or `建议修改` in the `问题` column because the two output parts already show the severity level.

## Required Checks

### Heading Format

Mark as `必须修改` when standard academic section headings are not capitalized correctly.

Examples:

- `introduction` -> `Introduction`
- `method` -> `Method` or `Methods`
- `results` -> `Results`
- `references` -> `References`

Check common headings such as `Abstract`, `Introduction`, `Literature Review`, `Method`, `Methods`, `Methodology`, `Results`, `Discussion`, `Conclusion`, `References`, and `Appendix`.

Mark as `建议修改` when heading capitalization or style is visibly inconsistent but not clearly wrong.

### Obvious Grammar Problems

Mark as `必须修改` when a sentence contains a clear grammar problem, including:

- subject-verb agreement errors;
- sentence fragments;
- run-on sentences that damage readability;
- incorrect tense that changes meaning;
- obvious article, singular/plural, or preposition errors;
- malformed sentence structure.

Only flag clear sentence-level errors. Do not treat every stylistic preference as a grammar problem.

### Unclear Pronoun Reference

Mark as `建议修改` when a pronoun has no clear referent or may refer to more than one previous noun or idea.

Common pronouns and demonstratives to check:

- `it`
- `this`
- `that`
- `they`
- `these`
- `those`
- `which`

In the `问题` column, identify the unclear pronoun itself. In the `修改方向` column, ask for the referent to be named explicitly or for the sentence to be restructured.

### Overclaiming

Mark as `建议修改` when a sentence or phrase makes an overly absolute, exaggerated, promotional, or unsupported claim.

Common signals include:

- `transformative`
- `groundbreaking`
- `revolutionary`
- `unprecedented`
- `paving the way`
- `poised to drive`
- broad claims about solving major field-level problems;
- strings of broad evaluative adjectives such as `interpretable, robust, and generalizable` when not supported by specific evidence.

Do not diagnose by keywords alone. Consider whether the sentence replaces concrete academic judgment with promotional rhetoric.

The revision direction should ask for a more specific, evidence-bounded academic claim. Prefer concrete scope, task type, condition, limitation, or future research direction.

Example revision direction:

`改为更具体、可验证的 final claim，说明该方法在哪些任务、条件或问题上具有潜力，或指出当前距离实用化还存在什么限制。`

### Rhetorical Questions at Paragraph or Section Openings

Mark as `建议修改` when a paragraph or section begins with a rhetorical question or question-answer style opening.

Examples:

- `So, why...?`
- `Why does this happen?`
- `What explains this phenomenon?`

Academic prose should normally use declarative topic sentences instead of rhetorical questions at the start of paragraphs or sections.

Do not flag formal research questions such as `RQ1:` or explicitly labeled research questions when they are part of the study design.

### Basic Academic Formatting and Formality

Mark as `建议修改` when visible academic convention problems appear, such as:

- figure/table labels not capitalized, such as `figure 1` instead of `Figure 1`;
- contractions in formal writing, such as `don't` or `can't`;
- clearly informal expressions, such as `a lot of`, `things`, or `get`, when a more academic alternative is needed;
- inconsistent formatting of repeated labels, headings, or section names.

Use `必须修改` only if the format problem is clearly required by academic convention or the user's stated rules.

## Output Format

Output only the modification items, split into two parts.

Use these two parts in this exact order:

### 必须修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [exact sentence, heading, phrase, or visible text segment]
   - 问题: [concise diagnosis in English]
   - 具体改进方向: [brief revision direction in English]

### 建议修改

1. Issue 1: [concise issue summary in English]
   - 原文句子: [exact sentence, heading, phrase, or visible text segment]
   - 问题: [concise diagnosis in English]
   - 具体改进方向: [brief revision direction in English]

Each issue must include:

- `原文句子`: the exact sentence, heading, phrase, or visible text segment that needs revision.
- `问题`: provide only a concise diagnosis in English. Do not begin with `必须修改：`, `建议修改：`, `必须修改`, or `建议修改`.
- `具体改进方向`: provide a brief direction for revision in English. A sample rewrite may be included only when it is short and directly useful.

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.

If one part has no items, write exactly `No items.` under that part's heading.

If there are no problems in either part, output:

### 必须修改

No items.

### 建议修改

No items.

## Constraints

Do not output unchanged content.

Do not add sections before, between, or after the two required parts.

Do not provide a general summary, score, or praise.

Do not rewrite the whole paper or paragraph unless the user explicitly requests rewriting after diagnosis.

Do not invent missing evidence, findings, sources, methods, or claims.

Do not diagnose Introduction, Methods, Discussion, Conclusion, or References move-step quality in depth. This skill is only for basic pre-checking.

Do not diagnose citation style, reference-list content, bibliography quality, or source relevance.
