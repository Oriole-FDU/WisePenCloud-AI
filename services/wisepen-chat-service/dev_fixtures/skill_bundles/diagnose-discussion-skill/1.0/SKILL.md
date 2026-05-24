---
name: diagnose-discussion-skill
description: Diagnoses the rhetorical move structure and coherence of academic Discussion sections using an 8-move Discussion framework based on research article discussion move studies. Use when Codex needs to check, evaluate, diagnose, or give pedagogical feedback on Discussion, Results and Discussion, Discussion and Conclusion, or review-article discussion passages, including missing or weak discussion moves, weak finding-explanation-claim logic, lack of connection to previous research, unsupported claims, missing limitations, or underdeveloped recommendations.
---

# Diagnose Discussion Skill

Use this skill to diagnose whether a student's academic Discussion section interprets findings or synthesized evidence, connects them to previous research, explains their meaning, and develops credible claims, limitations, and recommendations.

This skill is for academic English writing pedagogy. Produce diagnostic feedback, not grammar correction, style polishing, or a full rewrite unless the user explicitly asks for revision after diagnosis.

## Core Framework

Use the 8-move Discussion framework adapted from the user's source notes and `references/discussion-move-definitions.md`:

- Move 1: Information Move
- Move 2: Finding
- Move 3: Expected or Unexpected Outcome
- Move 4: Reference to Previous Research
- Move 5: Explanation
- Move 6: Claim
- Move 7: Limitation
- Move 8: Recommendation

A move is a broad rhetorical function in the Discussion. This skill currently treats each move as one primary step using the unified `M1S1`, `M2S1` format so it remains compatible with the shared discourse-diagnostic workflow.

For research papers, use `M2S1 Finding`, `M4S1 Reference to Previous Research`, and `M6S1 Claim` as the first-pass anchor moves. For review papers, interpret `M2S1` as a synthesized finding across studies and `M6S1` as a field-level claim.

## Required Workflow

When performing a real diagnosis, first read `references/workflow.md` and follow its sequence:

1. verify that the input is a Discussion-related section or passage;
2. identify discipline, article type, research tradition, evidence type, topic, and intended contribution;
3. segment the text by sentence-level functional units;
4. annotate each sentence with one primary move-step label and any secondary labels;
5. build a Peacock-informed discipline-sensitive expectation model, then adjust by article type;
6. judge expected move coverage using stable status labels;
7. diagnose Discussion-specific logic chains;
8. identify key missing or weak functions;
9. generate teaching-oriented feedback and revision priorities.

## Reference Files

Load only the files needed for the current task:

- `references/discussion-move-definitions.md`: read when assigning or explaining Discussion move labels.
- `references/discipline-sensitive-profiles.md`: read when deciding which moves are Core, Conventional, Conditional, or Not Applicable for a discipline, and when using the simplified Peacock-style profiles for research papers and review papers.
- `references/diagnostic-rubric.md`: read when judging Expectedness, Present, Weak, Missing, Not Applicable, Unclear, and revision priority.
- `references/logic-chain-rubric.md`: read when diagnosing Discussion coherence, including finding-explanation-claim and finding-limitation-recommendation chains.
- `references/output-templates.md`: read when formatting a full diagnostic report, short classroom feedback, sentence-level feedback, or coverage-only answer.
- `references/failure-strategies.md`: read when the input may not be a Discussion, is too short, lacks discipline metadata, or asks only for rewriting.
- `references/source-notes.md`: read when the user asks about the theoretical basis, citations, or why the skill uses this framework.

## Default Output

Unless the user asks for a shorter format, return seven sections:

1. Overall Diagnosis
2. Article Profile
3. Functional-Unit Move-Step Annotation
4. Expected Move Coverage
5. Discussion Logic Diagnosis
6. Key Missing or Weak Functions
7. Revision Priorities

In section 7, split the closing feedback into two tables, in this order:

- 必须修改
- 建议修改

Each table should use `原文句子 | 问题 | 为什么影响 Discussion | 具体改进方向`.

## Constraints

Do not:

- require all 8 moves in every Discussion;
- treat Optional / Enriching or Rare / Not Expected moves as missing problems;
- diagnose by keyword matching alone;
- replace rhetorical diagnosis with grammar correction;
- rewrite the whole Discussion unless requested;
- invent missing findings, literature, explanations, claims, limitations, recommendations, or implications;
- mark a move as Missing when an equivalent discipline-specific function is present;
- use the term "optional" in final feedback to criticize the student's text.

Do:

- focus on rhetorical function;
- preserve and quote original wording;
- use sentence-level units by default;
- allow one sentence to carry one primary label and multiple secondary labels;
- separate Expectedness, Status, and Confidence;
- distinguish Missing from Weak;
- judge move coverage and logic-chain coherence separately;
- explain why each issue matters for Discussion persuasiveness, interpretation, contribution, or reader trust;
- separate must-fix and suggested-fix items;
- provide concrete revision directions without inventing content for the student.



