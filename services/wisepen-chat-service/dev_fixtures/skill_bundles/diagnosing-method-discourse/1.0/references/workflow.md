# Method Discourse Diagnostic Workflow

This workflow diagnoses a submitted Methods section through the DRaC move-step model. It should produce teaching-oriented feedback, not grammar correction or full rewriting.

## 0. Input Gate

Confirm that the submitted text is a Methods section or a clearly identified methodology paragraph.

Stop the workflow when:

- the text is not from a Methods section;
- the text has no explicit or inferable method content;
- the user asks to diagnose a full paper but does not provide the Methods section.

If the Methods heading is absent but the passage clearly describes research design, data, procedure, tools, variables, or analysis, continue and state that the diagnosis is based on an inferred Methods passage.

## 1. Identify Discipline and Research Type

Infer or read from user metadata:

- discipline or broad field;
- research type;
- data type;
- method family;
- whether the study is empirical, experimental, qualitative, quantitative, mixed-methods, computational, review-based, or unclear.

Use this classification only to select teaching-required steps. Do not force all 16 DRaC steps onto every text.

## 2. Extract and Segment the Method Text

Extract only the Methods-related passage if the user provides surrounding content.

Segment the passage into functional units:

- usually sentence-level;
- split a sentence into clauses when different clauses perform different rhetorical functions;
- keep a sentence intact when splitting would obscure the student's logic.

Each unit must keep its original wording so feedback can point to exact evidence.

## 3. Annotate Moves and Steps

Assign one primary DRaC label to each functional unit and add secondary labels when a unit performs more than one function.

Use the detailed definitions and examples in `references/draC-move-step-definitions.md`. Keep only short DRaC definitions in `SKILL.md`.

For each unit record:

- text span;
- primary move-step label;
- secondary label(s), if any;
- confidence;
- brief reason.

Confidence scale:

- High: the rhetorical function is explicit.
- Medium: the function is likely but partly implicit.
- Low: the function is ambiguous or underdeveloped.

Feedback priority scale:

- 必须修改: absence or weakness would affect the method chain, credibility, validity, reproducibility, or reader trust.
- 建议修改: adding the detail would improve transparency, but the current text is not logically broken.
- 不必补充: the detail is unrelated to the study design or the current wording is already sufficient. Do not list these items in the final priority tables.

## 4. Build the Required-Step Profile

Using the discipline and research type from Step 1, decide which steps are teaching-required for this text.

Status labels must be:

- Present: clearly realized.
- Weak: mentioned but lacking necessary detail.
- Missing: required but absent.
- Not Applicable: does not fit the research design.
- Unclear: insufficient evidence to decide.

Avoid the word "optional" in the final diagnosis. Use "Not Applicable" when a step genuinely does not fit the design.

## 5. Evaluate Required-Step Coverage

Compare the annotated labels from Step 3 with the required profile from Step 4.

For each teaching-required step, decide its status and provide:

- evidence from the student's text;
- the problem, if any;
- why the issue matters for rigour, credibility, reproducibility, or reader trust.

Mark Present only when the rhetorical function is achieved, not merely when a keyword appears.

## 6. Detect Rationale Gaps

From Missing and Weak steps, identify methodological choices that are stated but not justified.

Look especially for choices involving:

- participants, samples, sites, materials, or datasets;
- tools, instruments, questionnaires, software, models, or corpora;
- variables, categories, groups, thresholds, and inclusion/exclusion criteria;
- cleaning, coding, transformation, normalization, or preprocessing;
- statistical, qualitative, computational, or evaluation methods.

For each gap, explain:

- which sentence or unit contains the choice;
- what choice needs justification;
- why readers need the rationale;
- what kind of rationale the student should add.

Do not invent the missing rationale.

## 7. Detect Method Logic Issues

Check the method chain:

Research aim/question -> research design -> data/participants/materials -> procedure/tools/variables -> data preparation -> data analysis -> credible findings.

Find logic problems such as:

- research question-method mismatch;
- data-analysis mismatch;
- participant-claim mismatch;
- tool-construct mismatch;
- variable-analysis mismatch;
- procedure-result mismatch;
- missing data preparation;
- unjustified analysis choice;
- sequence confusion;
- claims that exceed what the method can support.

Explain the issue through the affected sentence(s), affected DRaC step(s), and revision direction.

## 8. Convert Diagnosis into Teaching Feedback

Generate feedback in a teaching style:

- begin from what the text already does;
- identify missing, weak, or unclear rhetorical functions;
- explain why each issue affects credibility or reproducibility;
- before writing a feedback item, return to the original sentence or unit and confirm that the gap is real, the severity is correct, and the proposed fix still fits the study design;
- separate the closing feedback into two blocks: 必须修改 and 建议修改;
- tell the student what to add or clarify next;
- avoid replacing diagnosis with polished rewritten prose.

Preferred feedback pattern:

Your Methods section already does X. However, it does not yet do Y. This matters because Z. To revise, add or clarify A, B, and C.

## 9. Output Structure

Steps 1-8 are internal diagnostic work. They support the priority judgment, but they must not appear in the final response.

Final output must contain only the concrete revision priorities.

Do not output:

- Overall Diagnosis;
- Move-Step Annotation;
- Required Step Coverage;
- Unjustified Methodological Decisions;
- Method Logic Issues;
- `Revision Priorities` or any numbered section heading;
- paragraph summaries, scores, introductions, closing notes, or follow-up questions.

Output two blocks in this order:

- 必须修改: Missing steps and Weak items that materially affect rigour, credibility, reproducibility, validity, or method-chain logic. Re-check the original sentence before listing the item; if the evidence is only a minor transparency issue, downgrade it to 建议修改.
- 建议修改: Weak items and rationale gaps that would improve transparency, completeness, or reader trust but are not necessary to the method chain.
- 不必补充: items that are unrelated to the study design or already sufficiently covered. Do not include this in the final response; use it only to avoid over-diagnosis.

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each item must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English. Each item must be anchored in the original wording and should not invent content for the student.

