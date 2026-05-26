---
name: diagnosing-method-discourse
description: Diagnoses the rhetorical move-step structure of academic Methods sections using the DRaC model. Use when Codex needs to check, evaluate, diagnose, or give pedagogical feedback on Methods discourse, including move-step coverage, missing required steps, weak methodological explanation, unjustified method choices, or method-chain logic problems in academic English writing.
---

# Diagnosing Method Discourse

Use this skill to diagnose whether a student's academic Methods section demonstrates methodological rigour and credibility.

This skill is for academic English writing pedagogy. Produce diagnostic feedback, not grammar correction, style polishing, or a full rewrite unless the user explicitly asks for revision after diagnosis.

## Core Framework

Use the DRaC model: Demonstrating Rigour and Credibility.

Diagnose Methods discourse through three moves:

- Move 1: Contextualizing Study Methods
- Move 2: Describing the Study
- Move 3: Establishing Credibility

A move is a broad rhetorical function. A step is a more specific rhetorical action that realizes the move.

## Required Workflow

When performing a real diagnosis, first call `load_skill_asset` on `references/workflow.md` and follow its sequence.

For every workflow step below, you MUST call `load_skill_asset` for each listed file before completing that step. If the same file has already been loaded earlier in the current turn, reuse the already loaded content instead of calling `load_skill_asset` again.

1. verify that the input is a Methods section or methodology passage;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/failure-strategies.md`
2. identify discipline and research type;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discipline-required-profiles.md`
3. extract and segment the Methods text;
   - MUST load via `load_skill_asset`: `references/workflow.md`
4. annotate DRaC moves and steps;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/draC-move-step-definitions.md`
5. build the discipline-sensitive required-step profile;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discipline-required-profiles.md`
6. judge step status;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
7. detect rationale gaps;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
8. detect method-chain logic issues;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
9. generate teaching-oriented feedback and revision priorities.
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/output-templates.md`

## Reference Files

For a real Methods diagnosis, the following files are required by the workflow above and must be loaded through `load_skill_asset` when their workflow step is reached:

- `references/workflow.md`: required for every real Methods diagnosis.
- `references/failure-strategies.md`: required during input-gate and failure-mode handling.
- `references/discipline-required-profiles.md`: required when identifying article profile and deciding which steps are teaching-required for a discipline or research type.
- `references/draC-move-step-definitions.md`: required when assigning or explaining DRaC move-step labels.
- `references/diagnostic-rubric.md`: required when judging Present, Weak, Missing, Not Applicable, or Unclear, and when identifying rationale gaps or logic issues.
- `references/output-templates.md`: required when formatting the final revision-priority-only answer.

Conditional reference:

- `references/source-notes.md`: MUST load via `load_skill_asset` when the user asks about the theoretical basis, citations, source basis, or why the skill uses DRaC.

## Default Output

Unless the user explicitly asks for a different format, return only the final concrete revision priorities.

Do not include overall diagnosis, move-step annotation, required-step coverage, unjustified-decision tables, method-logic tables, diagnostic summaries, or closing notes in the final response. Use those analyses internally, then output only two blocks in this order:

- 必须修改
- 建议修改

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each issue must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.

## Constraints

Do not:

- invent missing methodological details;
- treat all 16 DRaC steps as universally required;
- diagnose by keyword matching alone;
- replace rhetorical diagnosis with grammar correction;
- rewrite the whole Methods section unless requested;
- mark a step as Missing when it is not applicable to the research design;
- use the term "optional" in the final diagnosis.

Do:

- focus on rhetorical function;
- use evidence from the student's text;
- distinguish Missing from Weak;
- identify decisions that need justification;
- identify logical breaks in the method chain;
- explain why each issue matters for rigour, credibility, or reproducibility;
- in the final feedback, separate must-fix and suggested-fix items, and re-check each one against the original wording before writing it;
- provide concrete revision priorities without inventing content for the student.

