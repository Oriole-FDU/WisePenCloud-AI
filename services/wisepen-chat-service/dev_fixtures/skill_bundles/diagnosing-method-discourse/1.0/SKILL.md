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

When performing a real diagnosis, first read `references/workflow.md` and follow its sequence:

1. verify that the input is a Methods section or methodology passage;
2. identify discipline and research type;
3. extract and segment the Methods text;
4. annotate DRaC moves and steps;
5. build the discipline-sensitive required-step profile;
6. judge step status;
7. detect rationale gaps;
8. detect method-chain logic issues;
9. generate teaching-oriented feedback and revision priorities.

## Reference Files

Load only the files needed for the current task:

- `references/draC-move-step-definitions.md`: read when assigning or explaining DRaC move-step labels.
- `references/discipline-required-profiles.md`: read when deciding which steps are teaching-required for a discipline or research type.
- `references/diagnostic-rubric.md`: read when judging Present, Weak, Missing, Not Applicable, or Unclear, and when identifying rationale gaps or logic issues.
- `references/output-templates.md`: read when formatting a full diagnostic report, short classroom feedback, or sentence-level feedback.
- `references/failure-strategies.md`: read when the input may not be a Methods section, is too short, lacks discipline metadata, or asks only for rewriting.
- `references/source-notes.md`: read when the user asks about the theoretical basis, citations, or why the skill uses DRaC.

## Default Output

Unless the user asks for a shorter format, return six sections:

1. Overall Diagnosis
2. Move-Step Annotation
3. Required Step Coverage
4. Unjustified Methodological Decisions
5. Method Logic Issues
6. Revision Priorities

In section 6, split the closing feedback into two tables, in this order:

- 必须补充
- 建议补充

Each table should use `原文句子 | 问题 | 具体改进方向`.

Use tables where they make the diagnosis easier to inspect.

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
- in the final feedback, separate must-add and suggested-add items, and re-check each one against the original wording before writing it;
- provide concrete revision priorities without inventing content for the student.

