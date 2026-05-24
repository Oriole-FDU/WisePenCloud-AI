# Conclusion Discourse Diagnostic Workflow

This workflow diagnoses a submitted Conclusion-related section through a three-move Conclusion framework. It should produce teaching-oriented feedback, not grammar correction or full rewriting.

## 0. Input Gate

Confirm that the submitted text can reasonably be diagnosed as a Conclusion-related passage.

Continue when:

- the text is explicitly headed `Conclusion`, `Conclusions`, `Concluding Remarks`, `Conclusion and Implications`, `Discussion and Conclusion`, or a close equivalent;
- the heading is absent but the passage clearly summarizes the study's main findings, evaluates significance, states implications, acknowledges limitations, recommends future work, or gives a final closing claim;
- the user provides a full paper and the Conclusion-related section can be cleanly extracted;
- the passage is short but still diagnosable, in which case state that the diagnosis is limited to the visible text.

Stop or ask for more input when:

- the text clearly belongs to another section and does not perform Conclusion functions;
- the user asks for a full Conclusion diagnosis but provides only a title, abstract, outline, table, figure caption, or isolated sentence;
- a full paper is provided but no Conclusion-related passage can be identified;
- the text has too little rhetorical content to support move-step diagnosis.

If the section type is uncertain, state the inference and mark later discipline- or section-specific judgments as provisional.

## 1. Identify Article Profile

Infer or read the profile from user metadata and the submitted text.

Use this profile only to select appropriate expectations. It is not a checklist.

| Profile dimension | What to identify |
|---|---|
| Discipline / subdiscipline | Broad field and, when possible, local field |
| Article type | Research paper, review paper, theoretical paper, technical paper, mixed paper, dissertation-like study, or unclear |
| Research tradition | Quantitative, qualitative, mixed, computational, laboratory, applied, conceptual, review-based, or unclear |
| Data / evidence type | Participants, corpus, experiment, dataset, model, texts, sources, cases, materials, or unclear |
| Topic / research object | The phenomenon, object, population, site, mechanism, method, model, or problem being concluded |
| Intended contribution | Finding, claim, intervention, implication, method, model, framework, synthesis, recommendation, or unclear |
| Confidence | High, Medium, or Low |

Identify discipline first. Use the closest profile in `references/discipline-expectedness-profiles.md` as the first filter for expectedness. Then adjust by article type.

If the exact discipline is unclear, map the text to the nearest broad family:

- Natural sciences / engineering
- Medical / life sciences
- Computer science / AI / applied technology
- Social sciences / education / applied linguistics / business
- Humanities / law / theoretical fields

If the fit is still weak, use cautious default expectations and mark discipline-sensitive judgments as provisional.

## 2. Segment the Conclusion into Sentence-Level Units

Segment the submitted text into sentence-level units before assigning move-step labels.

Rules:

- Use one sentence as one unit by default.
- Use `U1`, `U2`, `U3` numbering for sentence units.
- Do not split a sentence into clause units merely because it performs more than one step.
- Preserve original wording for every unit.
- Use the unit only as evidence; do not rewrite it during diagnosis.
- Split only when the user explicitly asks for clause-level analysis or when numbered/listed subparts function like independent sentences.

One sentence can count toward more than one step if the secondary function is rhetorically real, not merely implied by a keyword.

## 3. Annotate Moves and Steps

Use `references/move-step-definitions.md`.

For each sentence unit, assign:

- one primary move-step label for the dominant rhetorical function;
- secondary label(s), if the sentence also performs additional rhetorical functions;
- confidence;
- a short reason grounded in rhetorical function.

Use this table:

| Unit | Sentence | Primary label | Secondary label(s) | Confidence | Reason |
|---|---|---|---|---|---|
| U1 | ... | M1S3 | M2S1 | High | ... |

Confidence labels:

| Confidence | Meaning |
|---|---|
| High | The rhetorical function is explicit |
| Medium | The function is likely but partly implicit |
| Low | The function is ambiguous or underdeveloped |

Judge rhetorical function in context, not by keyword matching alone.

## 4. Build the Expectedness Model

Build expectedness primarily by discipline, then adjust by article type.

Use `references/discipline-expectedness-profiles.md`.

Use only these expectedness labels:

| Expectedness | Meaning | Can absence be criticized? |
|---|---|---|
| Core | A central function for this Conclusion type or discipline | Yes |
| Conventional | Normally expected in this discipline or article type | Yes |
| Strongly Expected | Highly expected; absence usually creates a serious weakness | Yes |
| Conditional | Expected only when triggered by the study, article type, local context, or prior section design | Yes, if triggered |
| Optional / Enriching | Useful but not required | No |
| Rare / Not Expected | Normally not expected | No |
| Not Applicable | Does not fit the study design, discipline, or article type | No |

Do not ask whether all steps appear. Ask which steps are pedagogically important for this text's discipline, article type, and research tradition.

For research-paper Conclusions, begin with this fast backbone check:

1. Is there a clear `M1S3 Synthesizing key findings`?
2. Does the Conclusion explain why the findings matter through `M2S1` and/or `M2S2`?
3. If the study has visible limitations or strong claims, does it set appropriate boundaries through `M2S3` or `M2S4`?
4. If future work is expected, does `M3S2` follow from the findings, limitations, or implications?
5. Does the final sentence give a meaningful close through `M3S3` or a clearly equivalent field-specific final judgment?

Article-type adjustments:

- Empirical research papers usually need finding synthesis and contribution or implication; limitations and future work depend on discipline and whether Discussion already handled them.
- Review papers need synthesis of literature patterns, field-level significance, literature limitations, and a grounded future agenda.
- Technical or computational papers need contribution, evidence quality, validation boundary, and concrete future improvement more than broad rhetorical flourish.
- Theoretical or conceptual papers may realize findings as arguments, propositions, interpretations, or framework claims rather than empirical results.
- Dissertation-like Conclusions are usually more complex and may require explicit limitations, recommendations, and future work, but this skill's v1 default is research articles.

Use `Optional / Enriching` and `Rare / Not Expected` internally, but do not criticize students for missing them in final feedback.

## 5. Evaluate Expected Move-Step Coverage

Use only these five status labels:

| Status | Definition |
|---|---|
| Present | The rhetorical function is clearly realized |
| Weak | The function is attempted but underdeveloped, vague, unsupported, poorly connected, or lacking necessary detail |
| Missing | A Core, Conventional, Strongly Expected, or triggered Conditional function is absent |
| Not Applicable | The function does not fit the study design, discipline, article type, or local context |
| Unclear | The available text is insufficient to decide whether the function is relevant or realized |

Keep three layers separate:

- Expectedness: whether a function should be checked.
- Status: how well the submitted text realizes that function.
- Confidence: how certain the annotator is about a sentence-level label.

Use this coverage table:

| Move / Step / Function | Expectedness | Status | Evidence | Problem | Why it matters |
|---|---|---|---|---|---|
| M1S3 Synthesizing key findings | Core | Present / Weak / Missing / Not Applicable / Unclear | ... | ... | ... |

Mark Present only when the rhetorical function is achieved, not merely when a keyword appears. Mark Missing only when the function is expected for this specific text.

## 6. Diagnose Conclusion Logic Chains

Read `references/logic-chain-rubric.md`.

Diagnose whether the Conclusion's rhetorical functions connect to each other. This is separate from move-step coverage.

Check logic chains that fit the text, such as:

- research purpose / problem -> key finding -> field advancement
- key finding -> significance -> implication / application
- limitation -> improvement -> future research
- review synthesis -> literature limitation -> future agenda
- technical / clinical finding -> validation boundary -> practical implication
- finding / synthesis -> final closing claim

Use this table:

| Logic chain | Evidence | Break / weak link | Why it weakens Conclusion | Revision direction |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

Do not force a chain that does not fit the article type or local passage.

## 7. Identify Key Missing or Weak Functions

Summarize the most important Missing, Weak, or Unclear functions.

Use this table:

| Function | Status | Evidence | Why it matters | Revision direction |
|---|---|---|---|---|
| M2S1 Significance / contribution | Weak | ... | ... | ... |

Prioritize issues that weaken Conclusion persuasiveness:

- the section repeats background but does not synthesize key findings;
- findings are summarized but not evaluated for significance or contribution;
- implications are generic or disconnected from findings;
- limitations are missing when the study's scope, evidence, or claims require boundaries;
- future research is generic and does not follow from findings or limitations;
- the final sentence ends abruptly without a closing judgment;
- the Conclusion introduces new findings or literature that should have been handled earlier.

Use the discipline profile to avoid over-diagnosing light steps. For example:

- in short hard-science Conclusions, do not force long literature dialogue or extensive limitation discussion when the findings and contribution are clear;
- in applied social sciences, education, and business, treat weak implications and weak future directions more seriously;
- in technical AI or engineering papers, treat missing validation boundaries or vague future improvements more seriously;
- in review papers, treat simple summary without synthesis as a serious weakness.

## 8. Convert Diagnosis into Teaching Feedback

Generate feedback in a teaching style:

- begin from what the Conclusion already does;
- identify missing, weak, or unclear rhetorical functions;
- explain why each issue affects synthesis, significance, credibility, impact, or closure;
- before writing a feedback item, return to the original sentence and confirm that the problem is real;
- separate the closing feedback into two blocks: 必须修改 and 建议修改;
- tell the student what kind of content to add or clarify next;
- avoid replacing diagnosis with polished rewritten prose.

Useful feedback pattern:

```text
Your Conclusion already does X. However, it does not yet do Y. This matters because Z. To revise, add or clarify A, B, and C.
```

Use this pattern only to decide the table content. Do not output a paragraph-style diagnosis unless the user explicitly asks for a separate explanation outside the skill's final format.

## 9. Output Structure

Steps 1-8 are internal diagnostic work. They support the priority judgment, but they must not appear in the final response.

Final output must contain only the content of `Revision Priorities`.

Do not output:

- `## 7. Revision Priorities` or any `Revision Priorities` heading;
- Overall Diagnosis;
- Article Profile;
- Sentence-Level Move-Step Annotation;
- Expected Move / Step Coverage;
- Conclusion Logic Diagnosis;
- Key Missing or Weak Functions;
- paragraph summaries, scores, introductions, closing notes, or follow-up questions.

Output two blocks in this order:

- 必须修改: missing expected steps and weak items that materially affect Conclusion synthesis, significance, credibility, implication, future direction, or closure.
- 建议修改: weak items that would improve specificity, evidence, connection, implication, or flow but do not break the core Conclusion logic.

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each item must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Conclusion when relevant]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English.

Each item must be anchored in original wording and should not invent content for the student. If one block has no items, keep the block and write `No items.` under it.
