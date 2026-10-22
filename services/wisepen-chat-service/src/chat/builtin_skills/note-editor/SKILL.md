---
name: note-editor
description: Use when the user asks to create, read, rewrite, polish, organize, summarize, insert, or delete content in a WisePen note, including notes that are not currently open. Provides the exact workflow and schemas for create_note, read_current_note_for_edit, and apply_current_note_edits.
---

# WisePen Note Editor

Use this skill whenever the task requires creating, understanding, or modifying a WisePen note. Work like a careful note editor: identify the intended note, inspect its latest structured snapshot, design the smallest correct block-level change, apply it with the returned version, and verify the result. A note does not need to be open in the frontend, but it must be visible to the current user.

## Core Editing Principles

1. Preserve the user's meaning, hierarchy, writing style, links, inline marks, tables, and unrelated content.
2. Never edit from memory or from an old conversation excerpt. Read the current note before planning a change.
3. Prefer the smallest sufficient diff. Replace content in an existing block when only its content changes; do not delete and recreate a block unnecessarily.
4. Keep one operation focused on one semantic change. Use several precise operations for several independent blocks.
5. Treat block ids and the note version as opaque values. Copy them exactly from the latest `read_current_note_for_edit` result.
6. Do not claim that the note was changed until `apply_current_note_edits` returns successfully.
7. If the user's request is ambiguous, destructive, or conflicts with the current note structure, ask a focused clarification question before applying changes.

## Expert Workflow

### 1. Understand the requested edit

Classify the request before touching the note:

- **Local rewrite or polish**: preserve the block and replace only its content.
- **Add content**: choose a meaningful existing block as the anchor and insert a new block before or after it.
- **Remove content**: delete only the explicitly targeted block. If a block contains mixed requested and unrelated content, replace its content instead of deleting the block.
- **Reorganize**: identify the affected blocks and preserve their content and hierarchy. There is no dedicated move operation; do not simulate a move unless the complete original content can be reconstructed safely.
- **Summarize or restructure**: first identify the requested destination and whether the user wants the original content retained. Do not silently destroy source material.

If the request names a heading, paragraph, list item, table, or phrase, locate that exact content in the current note XML and use its public block id. Do not infer ids from position, array order, or text.

### 2. Identify or create the target note

Use a Note `resource_id` from one of these trusted sources:

- `application_context.workspace_open_resource.resource_id` when the intended note is open;
- a Note result returned by `search_user_resources`;
- the result of `create_note` when the user asks to create a new note;
- an explicit Note selection supplied by the user or application.

Do not use `session_id`, a skill id, a user id, a guessed id, or an id copied from an unrelated context. If several notes match and the user's intended target is unclear, ask a focused clarification question.

To create an empty note, call `create_note` with this schema:

```json
{
  "title": "non-blank display title, required"
}
```

Keep the returned `resource_id`. Creating the resource does not write generated document content. To populate the new note, read it first and then apply one or more `appendRootBlock` operations using the returned version.

### 3. Read the target note

Call `read_current_note_for_edit` before every new edit, including the first edit of a newly created note.

The exact top-level call schema is:

```json
{
  "resource_id": "string, required",
  "scope": "object, optional",
  "include_ai_content": "boolean, optional",
  "version": "string, optional"
}
```

Rules for these parameters:

- `resource_id` must be a non-blank note resource id.
- Omit `scope` unless a scoped read is clearly needed. If a scope is needed, pass only a note read scope object whose fields are known from the runtime context; do not invent nested scope fields.
- `include_ai_content` is optional. Set it only when AI content is relevant to the requested edit.
- `version` is optional and may only reuse a version returned by an earlier read of the same note.

The successful result has this shape:

```json
{
  "resource_id": "the normalized resource id",
  "xml": "compact note XML string"
}
```

Read the returned `xml` to identify:

- public block ids,
- block types and attributes,
- block order and hierarchy,
- inline text, links, marks, math, and tables,
- the current `yjs-v1:` version.

The XML is the source of truth for the next edit. Do not use shorthand ids or create ids yourself.

### 4. Design a precise block-level diff

Choose the operation that matches the user's intent:

- Use `replaceContent` for rewriting text or inline/table content in an existing block.
- Use `deleteBlock` only for a whole-block deletion the user requested or clearly authorized.
- Use `insertBlock` for a genuinely new block relative to an existing block.
- Use `appendRootBlock` for a top-level block when an empty or newly created note has no suitable anchor.

For a polish request, preserve the original information and structure. Fix wording, grammar, clarity, and flow without adding unsupported facts. For an organizational request, keep existing headings and section boundaries unless the user asks for a new structure. For a summary, state whether the source remains and place the summary where it is useful.

Use `replaceContent` rather than delete-and-insert when:

- only wording changes;
- the block type or attributes should remain unchanged;
- the block contains inline marks, links, or other formatting that should be preserved.

Use `insertBlock` only when the new block's full content and type are known. Use the nearest stable existing block as `anchorBlockId`, and choose `position` as exactly `"before"` or `"after"`. Use `appendRootBlock` only when no suitable existing anchor is available; it appends one top-level suggestion block.

Do not use a made-up operation such as `move`, `update`, `append`, or `replaceText`. `appendRootBlock` is the only supported append operation. Do not send `block_id`, `op`, `content` as a string, or any other shorthand.

## Exact `apply_current_note_edits` Schema

Call this tool only after a successful `read_current_note_for_edit` for the same target note and only after constructing operations from that result.

The exact top-level call schema is:

```json
{
  "resource_id": "string, required",
  "patch_id": "string, required, stable idempotency id",
  "version": "string matching ^yjs-v1:, required",
  "operations": "array with 1 to 200 operation objects, required"
}
```

Top-level field rules:

- `resource_id` must be the same target note resource id used for the read.
- `patch_id` must identify this particular edit attempt. Reuse the same `patch_id` only when retrying the exact same operations; use a new id for a newly planned edit.
- `version` must be copied exactly from the latest `read_current_note_for_edit` result and must start with `yjs-v1:`.
- `operations` must contain at least one and at most 200 objects.
- Nested operation field names are case-sensitive camelCase. Top-level tool argument names are snake_case.

### Operation: replaceContent

```json
{
  "opId": "unique operation id within this patch",
  "kind": "replaceContent",
  "blockId": "public block id from read_current_note_for_edit",
  "content": {
    "kind": "inline",
    "items": [
      {
        "type": "text",
        "text": "replacement text"
      }
    ]
  }
}
```

`content` must be a structured content object, never a plain string.

### Operation: deleteBlock

```json
{
  "opId": "unique operation id within this patch",
  "kind": "deleteBlock",
  "blockId": "public block id from read_current_note_for_edit"
}
```

### Operation: insertBlock

```json
{
  "opId": "unique operation id within this patch",
  "kind": "insertBlock",
  "anchorBlockId": "public anchor block id from read_current_note_for_edit",
  "position": "before",
  "block": {
    "type": "paragraph",
    "attrs": {
      "key": "string, integer, number, or boolean value"
    },
    "content": {
      "kind": "inline",
      "items": [
        {
          "type": "text",
          "text": "new block text"
        }
      ]
    }
  }
}
```

`position` must be exactly `"before"` or `"after"`. `attrs` is optional; omit it when no block attributes are needed. `block.type` must be a non-empty string and must match the note's existing block conventions.

### Operation: appendRootBlock

```json
{
  "opId": "unique operation id within this patch",
  "kind": "appendRootBlock",
  "block": {
    "type": "paragraph",
    "content": {
      "kind": "inline",
      "items": [
        {
          "type": "text",
          "text": "first top-level block text"
        }
      ]
    }
  }
}
```

`appendRootBlock` has no `blockId`, `anchorBlockId`, or `position`. Use it for an empty or newly created note, or when the structured snapshot has no suitable anchor. Multiple top-level blocks require multiple operations with distinct `opId` values.

## Exact Structured Content Schema

Use one of these content objects:

### Inline content

```json
{
  "kind": "inline",
  "items": [
    {
      "type": "text",
      "text": "plain text",
      "marks": ["bold", "italic", "underline", "strike", "code"],
      "textColor": "optional color",
      "backgroundColor": "optional color"
    },
    {
      "type": "link",
      "text": "visible text",
      "href": "https://example.com",
      "marks": ["bold"]
    },
    {
      "type": "inlineMath",
      "expression": "a^2 + b^2 = c^2"
    }
  ]
}
```

Rules:

- `items` is an ordered list.
- Each item must have one of the exact `type` values `"text"`, `"link"`, or `"inlineMath"`.
- A `text` item requires `text`.
- A `link` item requires non-empty `href`.
- `marks`, `textColor`, and `backgroundColor` are optional. Use only the marks and styles observed in the current note or clearly requested by the user.
- Do not use `inlineMath` unless the content is actually mathematical.

### Table content

```json
{
  "kind": "table",
  "headerRows": 1,
  "headerCols": 1,
  "rows": [
    [
      [
        {
          "type": "text",
          "text": "cell text"
        }
      ]
    ]
  ]
}
```

Table nesting is exact: `rows` -> cells -> inline items. `headerRows` and `headerCols` are non-negative integers. Preserve the existing table dimensions and header settings unless the user explicitly requests a table change.

## Call Sequence and Recovery

Use this sequence:

1. Identify the target Note from application context, search results, or an explicit user selection; if the user requested a new Note, call `create_note` and use its returned `resource_id`.
2. Call `read_current_note_for_edit` for that `resource_id`.
3. Interpret the returned note XML and map the user's request to exact block ids. For an empty note with no suitable anchor, plan `appendRootBlock` operations instead.
4. Build a minimal, schema-valid operations array.
5. Call `apply_current_note_edits` with the same `resource_id` and the read version.
6. Inspect the result. Report only the changes confirmed by the tool.

If the apply call fails because the version is stale or the note changed:

1. Call `read_current_note_for_edit` again.
2. Re-locate the target blocks in the new XML.
3. Rebuild the entire plan using the new version and ids.
4. Use a new `patch_id` for the newly planned patch.

If the apply call fails with a schema or validation error, inspect the call against this document. Correct field names, casing, operation discriminators, structured content, and ids. Do not guess an alternative schema or silently fall back to editing text outside the note tools.

If there is no unambiguous target Note resource id, search for the requested Note or ask the user to select one. If the note cannot be read, do not call the apply tool. Do not require the Note to be opened in the frontend.

## Quality Bar

Before applying, verify:

- The operation matches the user's requested scope.
- Every `blockId` and `anchorBlockId` came from the latest read; `appendRootBlock` is used only when no suitable anchor exists.
- Every `opId` is unique within the patch.
- The version starts with `yjs-v1:` and came from the latest read.
- All content is structured with the exact `kind` and item `type` fields.
- Unchanged blocks are not included.
- No unsupported facts, invented formatting, or accidental deletion is introduced.

After applying, summarize the result in the user's language. Mention the affected section or content at a useful level, and do not expose raw internal ids unless they help diagnose a failure.
