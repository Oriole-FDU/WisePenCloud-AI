# Discipline-Sensitive Conclusion Profiles

Use this file to decide which Conclusion steps are easiest to judge and most pedagogically important for a given discipline and article type.

These profiles simplify diagnosis by starting from a shared Conclusion backbone and then narrowing the judgment by discipline.

## Shared Starting Point for Research Articles

Do not require all eleven steps.

For research-article Conclusions, begin with this shared backbone:

- `M1S3 Synthesizing key findings` is normally Core.
- `M2S1 Indicating significance / contribution` is normally Conventional.
- `M2S2 Drawing implications / applications` is Conventional in applied, clinical, policy, education, business, and technical fields, but may be lighter in some basic science articles.
- `M2S3 Indicating limitations / boundaries` is Conditional when the Discussion already handled limitations, but Conventional or Strongly Expected when claims are broad, evidence is narrow, or the field expects explicit caution.
- `M3S2 Recommending future research` is common but should not be required mechanically.
- `M3S3 Making an overall closing claim` is Conventional when the Conclusion otherwise ends abruptly.

Use the remaining steps selectively:

- `M1S1` and `M1S2` are often brief orientation moves, not required in every short Conclusion.
- `M1S4` is stronger in literature-dialogue and social-science fields.
- `M2S4` is especially important in technical, AI, computational, engineering, clinical, and methodological papers.
- `M3S1` is expected when clear limitations, technical constraints, or implementation weaknesses have been identified.

## Research Paper Profiles

### Natural Sciences / Physics / Materials / Environmental Science

- Core: `M1S3`, `M2S1`
- Conventional: `M3S3`
- Conditional: `M1S1`, `M1S2`, `M1S4`, `M2S2`, `M2S3`, `M2S4`, `M3S1`, `M3S2`
- Easy diagnostic focus: check whether the Conclusion distills the main result and states its contribution without over-expanding into a second Discussion.
- Common chains: `M1S3 -> M2S1 -> M3S3`; `M1S3 -> M2S1 -> M3S2`
- Avoid over-diagnosis: do not force heavy literature dialogue or long limitation sections when the article convention favors concise closure.

### Engineering / Applied Technology

- Core: `M1S3`, `M2S1`
- Conventional: `M2S2`, `M2S4`, `M3S1`, `M3S2`
- Conditional: `M1S1`, `M1S2`, `M1S4`, `M2S3`, `M3S3`
- Easy diagnostic focus: check whether technical performance, application value, validation boundary, and future improvement are specific.
- Common chains: `M1S3 -> M2S4 -> M3S1`; `M1S3 -> M2S2 -> M3S2`

### Computer Science / AI / Data-Driven Research

- Core: `M1S3`, `M2S1`, `M2S4`
- Conventional: `M2S3`, `M3S1`, `M3S2`
- Conditional: `M1S1`, `M1S2`, `M1S4`, `M2S2`, `M3S3`
- Easy diagnostic focus: check dataset/model/task specificity, validation boundaries, reliability limits, and concrete next research directions.
- Common chains: `M1S3 -> M2S4 -> M2S3 -> M3S1`; `M1S3 -> M2S1 -> M3S2`
- Treat missing boundaries seriously when the Conclusion makes broad claims about model capability, deployment, safety, or generalization.

### Medical / Health / Life Sciences

- Core: `M1S3`, `M2S2`, `M2S3`
- Strongly Expected: `M3S2` when evidence is preliminary, sample-bound, clinical, or translational
- Conventional: `M2S1`, `M2S4`, `M3S3`
- Conditional: `M1S1`, `M1S2`, `M1S4`, `M3S1`
- Easy diagnostic focus: check whether the Conclusion states clinical, biological, or practical meaning while setting appropriate evidence boundaries.
- Common chains: `M1S3 -> M2S2 -> M2S3 -> M3S2`; `M1S3 -> M2S4 -> M2S2`

### Social Sciences / Education / Applied Linguistics

- Core: `M1S3`, `M2S1`, `M2S2`
- Conventional: `M1S4`, `M2S3`, `M3S2`, `M3S3`
- Conditional: `M1S1`, `M1S2`, `M2S4`, `M3S1`
- Easy diagnostic focus: check whether findings are connected to literature, theory, pedagogy, policy, or social practice rather than summarized in isolation.
- Common chains: `M1S3 -> M1S4 -> M2S1/M2S2 -> M3S2`; `M2S3 -> M3S2`

### Business / Management / Economics / Public Administration

- Core: `M1S3`, `M2S1`, `M2S2`
- Conventional: `M3S2`, `M3S3`
- Conditional: `M1S1`, `M1S2`, `M1S4`, `M2S3`, `M2S4`, `M3S1`
- Easy diagnostic focus: check whether managerial, policy, market, or organizational implications follow from the findings.
- Common chains: `M1S3 -> M2S2 -> M3S3`; `M1S3 -> M2S3 -> M3S2`

### Humanities / Law / Theoretical Fields

- Core: `M1S3`, `M2S1`, `M3S3`
- Conventional: `M1S4`, `M2S2`
- Conditional: `M1S1`, `M1S2`, `M2S3`, `M2S4`, `M3S1`, `M3S2`
- Easy diagnostic focus: treat findings as arguments, interpretations, propositions, framework claims, or legal conclusions rather than experimental results.
- Common chains: `argument / synthesis -> contribution -> closing claim`; `interpretation -> implication -> future inquiry / action`
- Avoid over-diagnosis: do not force empirical limitation language if the article is conceptual or doctrinal, but check whether scope and applicability are clear.

## Review Paper Defaults

Review papers use the same moves, but the object of conclusion is the literature rather than one new dataset or experiment.

- Core: `M1S3 Synthesizing key findings`, `M2S1 Field-level significance / contribution`
- Conventional: `M1S4 Positioning against prior knowledge`, `M2S3 Literature or review limitations`, `M3S2 Future research agenda`, `M3S3 Closing claim`
- Conditional: `M1S1`, `M1S2`, `M2S2`, `M2S4`, `M3S1`
- Easy diagnostic focus: the Conclusion should synthesize patterns, evaluate the field's limits, position the review's contribution, and propose concrete future directions.
- Common chain: `M1S3 -> M2S1 -> M2S3 -> M3S2 -> M3S3`

Do not accept a review Conclusion that merely repeats section topics or citation lists without synthesis.

## Combined Discussion and Conclusion

When the passage is a combined Discussion and Conclusion:

- allow more explanation, literature comparison, and result interpretation than in a stand-alone Conclusion;
- still require a final looking-back and looking-forward closure if the passage is the article's final section;
- use `M1S3`, `M2S1/M2S2`, and `M3S2/M3S3` as the quickest final-section checks;
- do not penalize the Conclusion for lacking limitation or future research if those functions are clearly present earlier in the combined section.

## Dissertation-Like Conclusions

This skill's v1 default is research articles. If the user provides a dissertation or thesis Conclusion, state that the diagnosis is adapted.

For dissertations, treat these as more strongly expected:

- explicit research aim or question recap (`M1S1`);
- structured synthesis of findings (`M1S3`);
- contribution and implications (`M2S1`, `M2S2`);
- limitations (`M2S3`);
- future research (`M3S2`);
- final closing claim (`M3S3`).
