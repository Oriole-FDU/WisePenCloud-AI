# Translation Output Header Template

Prepend the following block (verbatim, filling in the placeholders) before the translated body:

```
【Paper Translation】
- Source language: <detected>
- Target language: <target>
- Scope: <full paper | abstract | section N | figure caption | references | ...>
- Notes: <terminology decisions, truncation warnings, or "none">
```

Rules:
- If any field is obvious/unambiguous, fill it briefly. Do not interrogate the user.
- If `Scope` cannot be inferred, use `fragment` and keep going.
- `Notes` should be "none" when there is nothing worth flagging; avoid filler.
