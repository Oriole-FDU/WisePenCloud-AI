# Discussion Discourse Diagnostic Workflow

This workflow diagnoses a submitted Discussion-related section through an 8-move Discussion framework. It should produce teaching-oriented feedback, not grammar correction or full rewriting.

## 0. Input Gate

Confirm that the submitted text can reasonably be diagnosed as a Discussion-related passage.

Continue when:

- the text is explicitly headed `Discussion`, `Results and Discussion`, `Discussion and Conclusion`, or a closely related heading;
- the heading is absent but the passage interprets findings, compares findings with previous research, explains outcomes, develops claims, states limitations, or proposes recommendations;
- the user provides a full paper and the Discussion-related section can be cleanly extracted;
- the passage is short but still diagnosable, in which case state that the diagnosis is limited to the visible text.

Stop or ask for more input when:

- the text clearly belongs to another section and does not perform Discussion functions;
- the user asks for a full Discussion diagnosis but provides only a title, abstract, outline, table, figure caption, or isolated sentence;
- a full paper is provided but no Discussion-related passage can be identified;
- the text has too little rhetorical content to support move diagnosis.

If the section type is uncertain, state the inference and mark later discipline- or section-specific judgments as provisional.

## 1. Identify Article Profile

Infer or read the profile from user metadata and the submitted text.

Use this profile only to select appropriate expectations. It is not a checklist.

| Profile dimension | What to identify |
|---|---|
| Discipline / subdiscipline | Broad field and, when possible, local field |
| Article type | Empirical, review, theoretical, technical, mixed-methods, computational, conceptual, or unclear |
| Research tradition | Quantitative, qualitative, mixed, computational, laboratory, applied, conceptual, review-based, or unclear |
| Data / evidence type | Participants, corpus, experiment, dataset, model, texts, sources, cases, materials, or unclear |
| Topic / research object | The phenomenon, object, population, site, mechanism, method, or problem being discussed |
| Intended contribution | Finding, claim, explanation, implication, method, model, framework, synthesis, recommendation, or unclear |
| Confidence | High, Medium, or Low |

Identify discipline first. Use the closest profile in `references/discipline-sensitive-profiles.md` as the first filter for expectedness. Then adjust by article type.

If the exact discipline is unclear, map the text to the nearest broad family:

- Natural sciences
- Social sciences / applied fields
- Language and humanities-like fields
- Law

If the fit is still weak, use cautious default expectations and mark discipline-sensitive judgments as provisional.

## 2. Segment the Discussion into Sentence-Level Units

Segment the submitted text into sentence-level functional units before assigning move labels.

Rules:

- Use one sentence as one unit by default.
- Use `U1`, `U2`, `U3` numbering for sentence units.
- Do not split a sentence into clause units merely because it performs more than one move.
- Preserve original wording for every unit.
- Use the unit only as evidence; do not rewrite it during diagnosis.
- Split only when the user explicitly asks for clause-level analysis or when numbered/listed subparts function like independent sentences.

One sentence can count toward more than one move if the secondary function is rhetorically real, not merely implied by a keyword.

## 3. Annotate Moves

Use `references/discussion-move-definitions.md`.

For each sentence unit, assign:

- one primary move-step label for the dominant rhetorical function;
- secondary label(s), if the sentence also performs additional rhetorical functions;
- confidence;
- a short reason grounded in rhetorical function.

Use this table:

| Unit | Text span | Primary label | Secondary label(s) | Confidence | Reason |
|---|---|---|---|---|---|
| U1 | ... | M2S1 | M4S1; M6S1 | High | ... |

Confidence labels:

| Confidence | Meaning |
|---|---|
| High | The rhetorical function is explicit |
| Medium | The function is likely but partly implicit |
| Low | The function is ambiguous or underdeveloped |

Judge rhetorical function in context, not by keyword matching alone.

## 4. Build the Expectedness Model

Build expectedness primarily by discipline, then adjust by article type.

Use `references/discipline-sensitive-profiles.md`.

Use only these expectedness labels:

| Expectedness | Meaning | Can absence be criticized? |
|---|---|---|
| Core | A central function for this Discussion type or discipline | Yes |
| Conventional | Normally expected in this discipline or article type | Yes |
| Strongly Expected | Highly expected; absence usually creates a serious weakness | Yes |
| Conditional | Expected only when triggered by findings, study design, article type, or local context | Yes, if triggered |
| Optional / Enriching | Useful but not required | No |
| Rare / Not Expected | Normally not expected | No |
| Not Applicable | Does not fit the study design, discipline, or article type | No |

Do not ask whether all eight moves appear. Ask which moves are pedagogically important for this text's discipline, article type, and research tradition.

For research papers, begin with Peacock's easiest first-pass check:

1. Is there a clear `M2S1 Finding` or evidence-based outcome being discussed?
2. Is there a clear `M6S1 Claim` about what the finding means?
3. In disciplines that rely on literature dialogue, is there `M4S1 Reference to Previous Research`?

Treat these as the fastest backbone check before judging the remaining moves.

Research-paper defaults:

- `M6S1 Claim`, `M2S1 Finding`, and `M4S1 Reference to Previous Research` are the main cross-disciplinary anchor moves.
- `M8S1 Recommendation` is common but should still be judged by discipline and article type.
- `M1S1`, `M3S1`, `M5S1`, and `M7S1` vary more strongly by discipline, finding type, and section design.

Article-type adjustments:

- Empirical Discussion usually depends on findings, explanation, previous research, claims, and often limitations.
- Results and Discussion sections may integrate result reporting with interpretation and previous research.
- Review-article discussion passages usually depend on synthesized findings, field-level claims, limitations of the literature, comparison with earlier reviews when relevant, and recommendations.
- Theoretical or conceptual Discussion passages may depend more on conceptual explanation, previous research, and claims than on empirical finding restatement.
- Discussion and Conclusion sections may make limitations, implications, and future directions more expected.

Use `Optional / Enriching` and `Rare / Not Expected` internally, but do not criticize students for missing them in final feedback.

## 5. Evaluate Expected Move Coverage

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

| Move / Function | Expectedness | Status | Evidence | Problem | Why it matters |
|---|---|---|---|---|---|
| M2S1 Finding | Core | Present / Weak / Missing / Not Applicable / Unclear | ... | ... | ... |

Mark Present only when the rhetorical function is achieved, not merely named. Mark Missing only when the function is expected for this specific text.

## 6. Diagnose Discussion Logic Chains

Read `references/logic-chain-rubric.md`.

Diagnose whether the Discussion's rhetorical functions connect to each other. This is separate from move coverage.

Check logic chains that fit the text, such as:

- Finding -> Explanation -> Claim
- Finding -> Previous Research -> Claim
- Finding -> Explanation -> Previous Research -> Claim
- Expected / Unexpected Outcome -> Explanation -> Claim
- Finding -> Limitation -> Recommendation
- Claim -> Implication -> Recommendation
- Review Synthesis -> Field-Level Claim -> Future Direction

Use the simplified family defaults when they fit:

- Natural sciences: Finding -> Expected / Unexpected Outcome -> Explanation -> Claim
- Social sciences / applied fields: Finding -> Previous Research -> Claim -> Recommendation
- Language and humanities-like fields: Finding -> Previous Research -> Claim
- Law: Evidence / Finding -> Argument cycle -> Claim
- Review paper: Synthesized Finding -> Field-Level Claim -> Limitation -> Recommendation

Use this table:

| Logic chain | Evidence | Break / weak link | Why it weakens Discussion | Revision direction |
|---|---|---|---|---|
| ... | ... | ... | ... | ... |

Do not force a chain that does not fit the article type or local passage.

## 7. Identify Key Missing or Weak Functions

Summarize the most important Missing, Weak, or Unclear functions.

Use this table:

| Function | Status | Evidence | Why it matters | Revision direction |
|---|---|---|---|---|
| M5S1 Explanation | Weak | ... | ... | ... |

Prioritize issues that weaken Discussion persuasiveness:

- finding is reported but not interpreted;
- finding is interpreted but not connected to previous research;
- previous research is mentioned but not used to show agreement, contrast, extension, or correction;
- explanation is vague or speculative without appropriate grounding;
- claim is too broad for the evidence;
- limitation is missing when needed for credibility;
- recommendation does not follow from the findings, limitations, or claim.

Use the discipline profile to avoid over-diagnosing light moves. For example:

- in physics, materials science, and environmental science, do not force heavy literature comparison or limitation statements when the section is brief and interpretation-focused;
- in language, linguistics, public administration, and related social fields, treat weak literature connection more seriously;
- in law, allow recursive argument cycles rather than forcing a single linear experimental pattern.

## 8. Convert Diagnosis into Teaching Feedback

Generate feedback in a teaching style:

- begin from what the Discussion already does;
- identify missing, weak, or unclear rhetorical functions;
- explain why each issue affects interpretation, contribution, coherence, credibility, or reader trust;
- before writing a feedback item, return to the original sentence and confirm that the problem is real;
- separate the closing feedback into two blocks: 必须修改 and 建议修改;
- tell the student what kind of content to add or clarify next;
- avoid replacing diagnosis with polished rewritten prose.

Useful feedback pattern:

```text
Your Discussion already does X. However, it does not yet do Y. This matters because Z. To revise, add or clarify A, B, and C.
```

## 9. Output Structure

Steps 1-8 are internal diagnostic work. They support the priority judgment, but they must not appear in the final response.

Final output must contain only the concrete revision priorities.

Do not output:

- Overall Diagnosis;
- Article Profile;
- Functional-Unit Move-Step Annotation;
- Expected Move Coverage;
- Discussion Logic Diagnosis;
- Key Missing or Weak Functions;
- `Revision Priorities` or any numbered section heading;
- paragraph summaries, scores, introductions, closing notes, or follow-up questions.

Output two blocks in this order:

- 必须修改: missing expected moves and weak items that materially affect Discussion logic, interpretation, contribution, credibility, or reader trust.
- 建议修改: weak items that would improve specificity, evidence, comparison, explanation, implication, or flow but do not break the core Discussion logic.

Use ordered lists for issues and unordered bullet points for item details. Do not use Markdown tables.

Each item must follow this structure:

1. Issue 1: [concise issue summary in English]
   - 原文句子: [original sentence or clause]
   - 问题: [diagnosis in English, including why it weakens the Discussion when relevant]
   - 具体改进方向: [specific revision direction in English]

Only the two block headings and fixed field labels may be Chinese. All issue summaries and explanatory content must be in English. Each item must be anchored in original wording and should not invent content for the student.
