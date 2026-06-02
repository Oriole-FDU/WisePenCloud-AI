# Discussion Failure Strategies

Use this file when the input is incomplete, ambiguous, or not suitable for Discussion diagnosis.

## Wrong Section

If the text is clearly not a Discussion-related passage, say so briefly and identify the likely section if possible.

Example response:

```text
This passage appears to function more like a Results section because it mainly reports data without interpretation, comparison with previous research, claims, limitations, or recommendations. A Discussion move diagnosis would be unreliable unless you provide the Discussion or Results and Discussion section.
```

If the passage has mixed functions, continue but state the inference:

```text
I will treat this as a Results and Discussion passage because it reports findings and immediately interprets them.
```

## Insufficient Text

If the user provides only a title, outline, figure caption, table, or isolated sentence, ask for more text unless they requested sentence-level labeling only.

If the text is short but diagnosable, proceed and say:

```text
The diagnosis is limited because only a short Discussion passage is visible.
```

## Missing Discipline Metadata

Infer discipline cautiously from topic, terminology, evidence type, and cited concepts.

If inference is weak:

- use a provisional profile;
- avoid strong discipline-specific claims;
- mark discipline-sensitive judgments as provisional.

## No Close Discipline Profile

If no close profile in `references/discipline-sensitive-profiles.md` fits the text, do not stop the diagnosis.

Map the text to the nearest broad family:

- Natural sciences
- Social sciences / applied fields
- Language and humanities-like fields
- Law

Then use article type and visible rhetorical needs to build a provisional expectedness model. State that the discipline match is approximate.

## Ambiguous Move Labels

When a sentence could fit more than one move:

- choose the dominant rhetorical function as the primary label;
- add clear secondary labels;
- use Medium or Low confidence when the function is implicit or underdeveloped;
- explain the ambiguity in the Reason column.

Do not split the sentence unless the user asks for clause-level analysis.

## Rewrite-Only Requests

If the user asks only to rewrite, polish, or improve a Discussion, do not perform a full move diagnosis unless useful.

Offer a brief diagnostic note first if the rhetorical problem is obvious, then revise only within the evidence provided. Do not invent findings, explanations, literature, limitations, or recommendations.
