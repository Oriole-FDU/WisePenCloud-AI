---
name: diagnosing-introduction-discourse
description: Diagnoses the rhetorical move-step structure of academic Introduction sections using Cotos and Pendar's 3-move/17-step framework with discipline-sensitive conventional steps. Use when Codex needs to check, evaluate, diagnose, or give pedagogical feedback on Introduction discourse, including missing conventional steps, weak gap or niche construction, gap-aim mismatch, source-list literature review, or discipline-appropriate research positioning in academic English writing.
---

# Diagnosing Introduction Discourse

Use this skill to diagnose whether a student's academic Introduction section establishes a credible territory, identifies a discipline-appropriate niche, and positions the present study clearly.

Produce diagnostic feedback, not grammar correction, style polishing, or a full rewrite unless the user explicitly asks for revision after diagnosis.

## Core Framework

Use Cotos and Pendar's Introduction model:

- Move 1: Establishing a Territory
- Move 2: Identifying a Niche
- Move 3: Addressing the Niche

A move is a broad rhetorical function. A step is a more specific rhetorical action that realizes the move.

## Required Workflow

When performing a real diagnosis, first call `load_skill_asset` on `references/workflow.md` and follow its sequence.

For every workflow step below, you MUST call `load_skill_asset` for each listed file before completing that step. If the same file has already been loaded earlier in the current turn, reuse the already loaded content instead of calling `load_skill_asset` again.

1. verify that the input is an Introduction section or opening research-positioning passage;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/failure-strategies.md`
2. identify discipline, article type, and research tradition;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discipline-sensitive-profiles.md`
3. extract and segment the Introduction into functional units;
   - MUST load via `load_skill_asset`: `references/workflow.md`
4. annotate moves and steps using Cotos and Pendar's label set;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/introduction-move-step-definitions.md`
5. build the discipline-sensitive conventional-step profile;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discipline-sensitive-profiles.md`
6. judge step coverage without requiring optional or rare steps;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
   - MUST load via `load_skill_asset`: `references/discipline-sensitive-profiles.md`
7. diagnose the gap or niche;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
   - MUST load via `load_skill_asset`: `references/introduction-move-step-definitions.md`
8. check gap-aim alignment;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
9. diagnose literature organization;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
10. generate teaching-oriented feedback and revision priorities.
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/output-templates.md`

## Reference Files

For a real Introduction diagnosis, the following files are required by the workflow above and must be loaded through `load_skill_asset` when their workflow step is reached:

- `references/workflow.md`: required for every real Introduction diagnosis.
- `references/failure-strategies.md`: required during input-gate and failure-mode handling.
- `references/discipline-sensitive-profiles.md`: required when identifying article profile and deciding which conventional steps or equivalent functions matter for a discipline.
- `references/introduction-move-step-definitions.md`: required when assigning or explaining Introduction move-step labels.
- `references/diagnostic-rubric.md`: required when judging Present, Weak, Missing, Not Applicable, or Unclear, and when diagnosing gap quality, gap-aim alignment, or literature organization.
- `references/output-templates.md`: required when formatting the final revision-priority-only answer.

Conditional reference:

- `references/source-notes.md`: MUST load via `load_skill_asset` when the user asks about the theoretical basis, citations, source basis, or why the skill uses this framework.

## Default Output

Unless the user explicitly asks for a different format, return only the final concrete revision priorities.

Do not include overall diagnosis, sentence-level move-step annotation, step coverage, gap or niche diagnosis, gap-aim alignment, literature-organization tables, diagnostic summaries, or closing notes in the final response. Use those analyses internally, then output only two blocks in this order:

- 必须修改
- 建议修改

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each issue must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens Introduction when relevant]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.

## Constraints

Do not:

- require all 17 steps;
- treat optional or rare steps as missing problems;
- diagnose by keyword matching alone;
- replace rhetorical diagnosis with grammar correction;
- rewrite the whole Introduction unless requested;
- invent missing literature, findings, aims, methods, or contributions;
- mark a step as Missing when an equivalent discipline-specific function is present;
- use the term "optional" in the final diagnosis to the student.

Do:

- focus on rhetorical function;
- quote the student's original sentence or clause for each problem;
- distinguish Missing from Weak;
- judge move coverage and step coverage separately;
- explain why each issue matters for territory, niche, present-study positioning, or disciplinary expectation;
- separate must-fix and suggested-fix items;
- provide concrete revision directions without inventing content for the student.

