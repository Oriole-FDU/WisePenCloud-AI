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

When performing a real diagnosis, first call `load_skill_asset` on `references/workflow.md` and follow its sequence.

For every workflow step below, you MUST call `load_skill_asset` for each listed file before completing that step. If the same file has already been loaded earlier in the current turn, reuse the already loaded content instead of calling `load_skill_asset` again.

1. verify that the input is a Discussion-related section or passage;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/failure-strategies.md`
2. identify discipline, article type, research tradition, evidence type, topic, and intended contribution;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discipline-sensitive-profiles.md`
3. segment the text by sentence-level functional units;
   - MUST load via `load_skill_asset`: `references/workflow.md`
4. annotate each sentence with one primary move-step label and any secondary labels;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discussion-move-definitions.md`
5. build a Peacock-informed discipline-sensitive expectation model, then adjust by article type;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discipline-sensitive-profiles.md`
6. judge expected move coverage using stable status labels;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
7. diagnose Discussion-specific logic chains;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/logic-chain-rubric.md`
8. identify key missing or weak functions;
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/discussion-move-definitions.md`
   - MUST load via `load_skill_asset`: `references/diagnostic-rubric.md`
9. generate teaching-oriented feedback and revision priorities.
   - MUST load via `load_skill_asset`: `references/workflow.md`
   - MUST load via `load_skill_asset`: `references/output-templates.md`

## Reference Files

For a real Discussion diagnosis, the following files are required by the workflow above and must be loaded through `load_skill_asset` when their workflow step is reached:

- `references/workflow.md`: required for every real Discussion diagnosis.
- `references/failure-strategies.md`: required during input-gate and failure-mode handling.
- `references/discipline-sensitive-profiles.md`: required when identifying article profile and deciding which moves are Core, Conventional, Conditional, or Not Applicable for a discipline and article type.
- `references/discussion-move-definitions.md`: required when assigning or explaining Discussion move labels.
- `references/diagnostic-rubric.md`: required when judging Expectedness, Present, Weak, Missing, Not Applicable, Unclear, and revision priority.
- `references/logic-chain-rubric.md`: required when diagnosing Discussion coherence, including finding-explanation-claim and finding-limitation-recommendation chains.
- `references/output-templates.md`: required when formatting the final revision-priority-only answer.

Conditional reference:

- `references/source-notes.md`: MUST load via `load_skill_asset` when the user asks about the theoretical basis, citations, source basis, or why the skill uses this framework.

## Default Output

Unless the user explicitly asks for a different format, return only the final concrete revision priorities.

Do not include overall diagnosis, article profile, move-step annotation, coverage tables, logic diagnosis, missing/weak-function tables, diagnostic summaries, or closing notes in the final response. Use those analyses internally, then output only two blocks in this order:

- 必须修改
- 建议修改

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each issue must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens Discussion when relevant]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.

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



