---
name: diagnose-conclusion-skill
description: Diagnoses the rhetorical move-step structure and coherence of academic Conclusion, Conclusions, Concluding Remarks, or final Discussion-and-Conclusion passages using a three-move Conclusion framework. Use when Codex needs to check, evaluate, diagnose, or give pedagogical feedback on Conclusion discourse, including missing or weak key-finding synthesis, contribution or significance, implications, limitations, improvements, future research, closing claim, discipline-sensitive expectations, or weak looking-back to looking-forward logic in academic English writing.
---

# Diagnose Conclusion Skill

Use this skill to diagnose whether a student's academic Conclusion section consolidates the study, evaluates its contribution and boundaries, and projects credible future directions.

This skill is for academic English writing pedagogy. Produce diagnostic feedback, not grammar correction, style polishing, or a full rewrite unless the user explicitly asks for revision after diagnosis.

## Core Framework

Use the Conclusion framework adapted from Yang and Allison's three-move model, later Conclusion(s) studies, the user's local teaching materials, and the WisePen discourse-diagnostic workflow:

- Move 1: Consolidating the Study
- Move 2: Evaluating the Study
- Move 3: Projecting Forward

A move is a broad rhetorical function in the Conclusion. A step is a more specific rhetorical action. Use the unified `M1S1`, `M1S2`, `M2S1` format.

For research articles, use `M1S3 Synthesizing key findings` as the first-pass anchor step. Then check whether the Conclusion explains why the findings matter through `M2S1` and/or `M2S2`, and whether any limitation, improvement, future direction, or closing claim is expected for the discipline and article type.

For review papers, interpret `M1S3` as a synthesized pattern across the reviewed literature, `M2S3` as a limitation of the literature or review, and `M3S2` as a grounded future research agenda.

## Required Workflow

When performing a real diagnosis, first read `references/workflow.md` and follow its sequence:

1. verify that the input is a Conclusion-related section or passage;
2. identify discipline, article type, research tradition, evidence type, topic, and intended contribution;
3. segment the text into sentence-level units;
4. annotate each sentence with one primary move-step label and any secondary labels;
5. select a discipline-sensitive expectedness profile, then adjust by article type;
6. judge expected move-step coverage using stable status labels;
7. diagnose Conclusion-specific logic chains;
8. identify key missing or weak functions;
9. generate teaching-oriented feedback and revision priorities.

## Reference Files

Load only the files needed for the current task:

- `references/move-step-definitions.md`: read when assigning or explaining Conclusion move-step labels.
- `references/discipline-expectedness-profiles.md`: read when deciding which steps are Core, Conventional, Conditional, Optional / Enriching, Rare / Not Expected, or Not Applicable for a discipline and article type.
- `references/diagnostic-rubric.md`: read when judging Expectedness, Present, Weak, Missing, Not Applicable, Unclear, and revision priority.
- `references/logic-chain-rubric.md`: read when diagnosing looking-back to looking-forward coherence, including finding-significance-implication and limitation-improvement-future chains.
- `references/output-templates.md`: read when formatting the final revision-priority-only answer.
- `references/failure-strategies.md`: read when the input may not be a Conclusion, is too short, lacks discipline metadata, or asks only for rewriting.
- `references/source-notes.md`: read when the user asks about the theoretical basis, citations, or why the skill uses this framework.

## Default Output

Always perform the full diagnostic workflow internally, but the final response must output only the content of `Revision Priorities`.

Do not output:

- `## 7. Revision Priorities` or any `Revision Priorities` heading;
- Overall Diagnosis;
- Article Profile;
- Sentence-Level Move-Step Annotation;
- Expected Move / Step Coverage;
- Conclusion Logic Diagnosis;
- Key Missing or Weak Functions;
- scores, summaries, introductions, closing remarks, or follow-up questions.

The final response must contain only two tables, in this order:

- 必须修改
- 建议修改

Each table should use `原文句子 | 问题 | 为什么影响 Conclusion | 具体改进方向`.

## Constraints

Do not:

- require all Conclusion steps in every text;
- treat Optional / Enriching or Rare / Not Expected steps as missing problems;
- diagnose by keyword matching alone;
- replace rhetorical diagnosis with grammar correction;
- rewrite the whole Conclusion unless requested;
- invent missing findings, literature, limitations, implications, applications, improvements, future directions, or contributions;
- mark a function as Missing when an equivalent discipline-specific function is present;
- force dissertation-style requirements onto research-article Conclusions;
- use the term "optional" in final feedback to criticize the student's text.

Do:

- focus on rhetorical function;
- preserve and quote original wording;
- use sentence-level units by default;
- allow one sentence to carry one primary label and multiple secondary labels;
- separate Expectedness, Status, and Confidence;
- distinguish Missing from Weak;
- judge move-step coverage and logic-chain coherence separately;
- explain why each issue matters for Conclusion synthesis, significance, credibility, impact, or closure;
- separate must-fix and suggested-fix items;
- provide concrete revision directions without inventing content for the student.

