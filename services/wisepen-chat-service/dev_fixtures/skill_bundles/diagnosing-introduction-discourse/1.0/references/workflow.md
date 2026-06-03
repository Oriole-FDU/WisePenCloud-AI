# Introduction Discourse Diagnostic Workflow

This workflow diagnoses a submitted Introduction through Cotos and Pendar's 3-move/17-step model. It should produce teaching-oriented feedback, not grammar correction or full rewriting.

## 0. Input Gate

Confirm that the submitted text is an Introduction section or a clearly identifiable opening passage that establishes research territory, niche, and present-study positioning.

Continue when:

- the text is explicitly an Introduction section;
- the text has no heading but clearly performs Introduction functions;
- the user provides a full paper and the Introduction can be cleanly extracted.

Stop or ask for more input when:

- the passage is not an Introduction or related opening section;
- the user asks for a full Introduction diagnosis but provides only a title, abstract, outline, or isolated sentence;
- the full paper has no identifiable Introduction.

If the passage is short but diagnosable, continue and state that the diagnosis is limited to the visible text.

## 1. Identify Article Profile

Infer or read from user metadata:

- discipline or subdiscipline;
- article type: empirical, experimental, theoretical, review, technical, mixed-methods, or unclear;
- research tradition: quantitative, qualitative, mixed, computational, laboratory, applied, conceptual, or unclear;
- likely topic, research object, and intended contribution.

Use this profile only to select discipline-sensitive expectations. If the discipline is unclear, use the default profile in `references/discipline-sensitive-profiles.md` and mark discipline-specific judgments as provisional.

## 2. Extract and Segment the Introduction

Extract only the Introduction-related passage if the user provides surrounding text.

Segment the Introduction into functional units:

- usually sentence-level;
- split a sentence into clauses when different clauses perform different rhetorical functions;
- keep a sentence intact when splitting would obscure the student's logic;
- preserve original wording for every unit.

Each unit should receive an ID such as `S1`, `S2`, or `S3a`.

## 3. Annotate Moves and Steps

Use the detailed labels in `references/introduction-move-step-definitions.md`.

For each unit record:

- text span;
- primary move-step label;
- secondary label(s), if any;
- confidence: High, Medium, or Low;
- brief reason.

Judge rhetorical function in context, not keywords alone. A sentence can realize more than one step.

## 4. Build the Discipline-Sensitive Conventional Profile

Read `references/discipline-sensitive-profiles.md`.

Use only conventional steps or equivalent functions as teaching-required checks. Do not require optional or rare steps, and do not list them as missing in the final diagnosis.

Use equivalent-function logic where appropriate:

- Default niche: M2S4 Indicating a gap or M2S5 Highlighting a problem.
- Default present study: M3S9 Introducing present research descriptively or M3S10 Introducing present research purposefully.
- Conservation Biology: a real-world conservation problem can realize the niche even without a pure literature gap.
- Chemical Biology: principal outcomes plus value can realize Move 3 even when an explicit purpose sentence is absent.
- Wildlife Behavior: species/site/research-object background may be required local background.
- Social Sciences: research questions or hypotheses may become important depending on subfield and method.

## 5. Evaluate Conventional-Step Coverage

Use these final status labels:

- Present
- Weak
- Missing
- Not Applicable
- Unclear

For each conventional step or equivalent function, decide its status and provide evidence from the student's wording.

Mark Present only when the rhetorical function is achieved, not merely when a keyword appears.

Put Missing items and serious Weak items into `必须修改` when they harm one of these Introduction functions:

- establishing a credible research territory;
- identifying a clear niche, gap, problem, unresolved question, or debate;
- introducing the present study in a way that addresses the niche;
- showing the value, contribution, method, outcome, or research question expected by the discipline.

Put less serious Weak items into `建议修改` when they mainly need sharper wording, clearer support, or stronger synthesis.

## 6. Diagnose the Gap or Niche

Identify how the Introduction builds its niche.

Possible gap or niche types:

- literature gap;
- methodological or technical limitation;
- conceptual or theoretical gap;
- empirical gap involving population, dataset, context, setting, species, site, period, or material;
- unresolved mechanism;
- practical, social, clinical, environmental, or conservation problem;
- unresolved debate or contradiction in previous findings;
- broad research question or hypothesis that motivates the study.

Evaluate whether the gap is:

- specific enough to guide the study;
- credible and supported by reviewed literature;
- naturally developed from previous sentences;
- appropriately scoped for the article;
- aligned with the discipline's normal niche-building strategy;
- not overstated beyond what the cited literature can support.

Do not invent a better gap. Tell the student what kind of gap, support, or narrowing the text needs.

## 7. Check Gap-Aim Alignment

Extract and compare:

- gap, problem, debate, or unresolved question;
- research aim, objective, research question, hypothesis, contribution, or value claim;
- method preview, outcome preview, or principal finding when the discipline expects them.

Ask:

- Does the aim answer the same gap that the Introduction created?
- Do the population, object, context, variables, method, and scope match?
- Does the method preview look capable of addressing the stated gap?
- Does the contribution or value claim follow from the gap?
- Is the aim too broad, too narrow, or shifted to a different problem?

When gap and aim do not correspond, quote both the gap sentence and the aim or contribution sentence.

## 8. Diagnose Literature Organization

Check whether the literature review is content-driven rather than source-driven.

Problem patterns include:

- source list pattern: A says X, B says Y, C says Z without synthesis;
- isolated study summaries without relationships among them;
- no contrast, development, grouping, or tension across studies;
- previous research does not lead to the gap;
- claims of importance or background lack source support;
- reviewed studies are irrelevant to the territory or niche;
- citation-heavy sentences replace the writer's own synthesis.

Treat literature organization as `必须修改` when it prevents the reader from seeing the research gap or niche. Treat it as `建议修改` when the review is understandable but could be grouped more clearly by theme, method, population, finding, or limitation.

## 9. Convert Diagnosis into Teaching Feedback

Use `references/output-templates.md` for formatting.

Steps 1-8 are internal diagnostic work. They support the priority judgment, but they must not appear in the final response.

Final output must contain only the concrete revision priorities.

Do not output:

- Overall Diagnosis;
- Sentence-Level Move-Step Annotation;
- Discipline-Sensitive Step Coverage;
- Gap or Niche Diagnosis;
- Gap-Aim Alignment;
- Literature Organization;
- `Revision Priorities` or any numbered section heading;
- paragraph summaries, scores, introductions, closing notes, or follow-up questions.

Output two blocks in this order:

- 必须修改: missing conventional steps and weak items that materially harm Introduction logic, gap construction, gap-aim fit, or discipline-expected positioning.
- 建议修改: weak items that would improve specificity, synthesis, citation support, or rhetorical clarity but do not break the Introduction.

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each item must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Introduction when relevant]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English. Each item must be anchored in original wording and should not invent content for the student.
