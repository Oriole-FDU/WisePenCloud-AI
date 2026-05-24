---
name: reviewing-reference-use
description: Reviews student academic writing for citation style, reference-list consistency, and source relevance. Use when Codex needs to check references, bibliographies, works cited pages, in-text citations, discipline-appropriate citation style, missing or uncited references, malformed reference entries, or whether cited sources are relevant to a student's article.
---

# Reviewing Reference Use

Use this skill to diagnose how a student article uses sources. Focus on citation style fit, internal consistency, in-text/reference-list matching, and whether the cited sources are relevant to the article's topic and argument.

Produce diagnostic feedback and safe correction guidance, not a complete rewritten bibliography unless the user explicitly asks for one.

## Required Workflow

For a real review, first read `references/workflow.md` and follow its sequence:

1. validate the input and extract citation data;
2. identify the article's discipline, article type, and review scope;
3. select the expected citation style from explicit requirements first, then discipline conventions;
4. detect the actual in-text citation style and reference-list style;
5. check style fit, consistency, and in-text/reference-list correspondence;
6. suggest safe corrections without inventing missing bibliographic metadata;
7. assess reference relevance from title, citation context, and, when needed and available, web-verified abstracts or introductions;
8. produce the final reference review report.

## Reference Files

Load only the files needed for the current task:

- `references/workflow.md`: read for every full reference review.
- `references/citation-style-guide.md`: read when classifying citation style, judging discipline fit, or correcting format issues.
- `references/relevance-rubric.md`: read when judging whether references match the student's topic, research question, or citation context.
- `references/output-templates.md`: read when formatting a full report, short report, or correction-only answer.
- `references/failure-strategies.md`: read when the input lacks a reference list, lacks in-text citations, is interdisciplinary, has mixed styles, or cannot be web-verified.
- `references/source-notes.md`: read when the user asks about the basis of the citation rules or when a style-specific rule is uncertain.

## Default Output

Unless the user asks for a shorter answer, return seven sections:

1. Discipline Identification
2. Expected Citation Style
3. Detected Citation Style
4. Format Problems
5. In-text Citation and Reference List Matching
6. Reference Relevance Review
7. Priority Revision Suggestions

Use tables for issue lists. Split final priorities into must-fix and suggested-fix items when there are enough issues to justify the distinction.

## Constraints

Do not:

- invent authors, titles, years, journal names, publishers, DOIs, URLs, page ranges, or abstracts;
- treat a discipline inference as stronger than an assignment, course, journal, or instructor requirement;
- force APA, MLA, or Chicago onto fields where numbered, legal, chemical, or biomedical styles are more plausible;
- judge a reference as irrelevant from title alone when the title is ambiguous;
- claim a source was checked online unless it was actually searched and the source used is named;
- silently normalize a mixed style without explaining what was mixed.

Do:

- use the student's requested language unless the user asks otherwise;
- preserve evidence from the student's text in every diagnosis;
- distinguish expected style, actual style, and acceptable alternatives;
- classify uncertain cases explicitly as uncertain;
- prioritize consistency, traceability, and discipline appropriateness;
- mark missing bibliographic details as needing manual verification unless they are verified from a reliable source;
- when using web search, cite the consulted source and explain what was verified.
