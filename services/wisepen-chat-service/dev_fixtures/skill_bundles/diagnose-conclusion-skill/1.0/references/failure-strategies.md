# Conclusion Failure Strategies

Use this file when the submitted input does not cleanly fit the Conclusion diagnostic workflow.

## Wrong Section

If the passage is clearly Introduction, Methods, Results, Literature Review, or a non-final Discussion passage:

- state that the passage is not suitable for a Conclusion diagnosis;
- briefly identify the likely section type and why;
- if another WisePen skill fits, suggest the appropriate diagnostic direction;
- do not force Conclusion labels onto the passage.

Example response:

```text
This passage reads as a Discussion rather than a Conclusion because it interprets individual findings and compares them with previous studies, but it does not yet perform final synthesis or closure. A Conclusion diagnosis would be unreliable unless you provide the final section.
```

## Combined Discussion and Conclusion

If the passage is headed `Discussion and Conclusion`, `Results and Discussion`, or otherwise blends interpretation and closure:

- continue if the passage includes final synthesis, implications, limitations, future work, or closing claim;
- state that the diagnosis treats the passage as a combined final section;
- allow Discussion-like explanation and literature comparison;
- still diagnose whether the final part performs Conclusion closure.

Do not criticize the passage for being longer or more interpretive than a stand-alone Conclusion when the heading signals a combined section.

## Full Paper Provided

If the user provides a full paper:

- extract only the section headed `Conclusion`, `Conclusions`, `Concluding Remarks`, `Conclusion and Implications`, or a close equivalent;
- if no heading exists, identify the final section or final paragraphs that perform Conclusion functions;
- state the extraction decision;
- do not diagnose the whole paper as if every final paragraph were a Conclusion if the boundary is unclear.

If the Conclusion cannot be identified, ask for the Conclusion section.

## Too Short or Insufficient Text

If the input is a title, abstract, outline, bullet list, single sentence, or fragment:

- do not produce a full diagnosis;
- explain that there is too little rhetorical content for move-step coverage;
- offer a limited comment only if at least one Conclusion function is visible.

If the passage is short but diagnosable, continue and mark the diagnosis as limited.

## Missing Discipline Metadata

If the discipline is not provided:

- infer the broad discipline from topic, terminology, method, and article type;
- choose the nearest broad expectedness profile;
- mark discipline-sensitive judgments as provisional;
- avoid strong criticism for steps whose expectedness depends heavily on discipline.

Do not ask for metadata when the text itself gives enough context for a cautious diagnosis.

## Ambiguous Article Type

If it is unclear whether the text is a research paper, review paper, theoretical paper, or dissertation-like Conclusion:

- infer from cues such as `this study`, `review`, `literature`, `participants`, `dataset`, `model`, `experiment`, or `framework`;
- state the inference in the Article Profile;
- apply the closest profile cautiously;
- mark uncertain coverage judgments as `Unclear` rather than `Missing` when expectedness depends on article type.

## Rewrite-Only Requests

If the user only asks to rewrite a Conclusion:

- ask whether they want diagnosis first only when the request is ambiguous and a diagnosis would materially change the rewrite;
- otherwise, if this skill is already triggered, provide a brief rhetorical diagnosis before any suggested revision;
- do not invent findings, limitations, contributions, or future work;
- if rewriting is requested, keep added content in placeholders or ask for missing study details when necessary.

## New Content Risk

Never invent:

- findings or results;
- literature claims;
- methodological details;
- limitations;
- future research directions;
- practical applications;
- contribution claims.

When a needed function is missing, tell the student what kind of information to add and where it should connect, but do not fabricate the content.
