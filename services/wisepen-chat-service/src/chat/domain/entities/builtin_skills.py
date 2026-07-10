from __future__ import annotations

from chat.domain.entities.skill import Skill, SkillMeta


NOTE_AI_DIFF_SKILL_ID = "wisepen-note-ai-diff"
NOTE_AI_DIFF_SKILL_NAME = "WisePen Note AI-Diff"
NOTE_AI_DIFF_SKILL_DESCRIPTION = (
    "Strict workflow for editing the current WisePen note through AI-Diff tools."
)
NOTE_AI_DIFF_SKILL_VERSION = 1
NOTE_AI_DIFF_SKILL_MD = """
# WisePen Note AI-Diff

Use this skill when the user asks to inspect, edit, polish, translate, shorten,
expand, correct, restructure, or otherwise modify the currently opened WisePen
note through AI-Diff review suggestions.

## Required Workflow

1. Call `read_note_aixml` before every note edit.
2. If the application context includes selected note text, call
   `read_note_aixml` with `scope: "selected_note_scope"` first unless the user
   asks for broader whole-note context.
3. Use only ids that appear in the latest `<ai_xml>` returned by the tool.
4. Build a strict AI-Diff JSON plan with top-level `version: 1` and an
   `operations` array.
5. Call `apply_current_note_ai_diff_plan` with the exact `export_handle`
   returned by `read_note_aixml`.
6. After apply succeeds, briefly tell the user what was inserted as AI-Diff
   suggestions. Mention conflicts or skipped operations at a high level.

## Hard Rules

- Do not answer questions about current note content from context alone. Read
  the note with `read_note_aixml`.
- Do not call apply before reading the note.
- In exact selected-text edits, `replace_text.text` must be the full target text
  with only the selected span replaced: `prefix + transformed_selected_text +
  suffix`.
- Do not keep the original selected span next to its translation, rewrite, or
  correction unless the user explicitly asks to keep it.
- Do not repeat the transformed selected span. For translation or rewrite
  requests, the transformed text must appear exactly once in the affected target
  unless the user explicitly asks for repetition.
- Never invent block ids, text ids, link ids, math ids, hashes, paths, styles,
  content indexes, BlockNote JSON, or Yjs paths.
- Never submit XML as the patch. The apply tool accepts only strict JSON.
- The apply tool writes review suggestions. It does not directly accept or
  permanently rewrite final text.

## Valid Operation Kinds

- `replace_text`
- `replace_link`
- `replace_inline_math`
- `replace_math_expression`
- `add_text`
- `add_link`
- `add_inline_math`
- `add_block`
- `delete_target`
- `delete_block`

Every operation must include a unique non-empty `opId` and the required fields
for its `kind`. Use the tool schema as the source of truth for field names.
""".strip()


def build_note_ai_diff_skill_meta() -> SkillMeta:
    return SkillMeta(
        skill_id=NOTE_AI_DIFF_SKILL_ID,
        name=NOTE_AI_DIFF_SKILL_NAME,
        description=NOTE_AI_DIFF_SKILL_DESCRIPTION,
        version=NOTE_AI_DIFF_SKILL_VERSION,
    )


def build_note_ai_diff_skill() -> Skill:
    return Skill(
        skill_id=NOTE_AI_DIFF_SKILL_ID,
        name=NOTE_AI_DIFF_SKILL_NAME,
        description=NOTE_AI_DIFF_SKILL_DESCRIPTION,
        source_type="BUILTIN",
        skill_md=NOTE_AI_DIFF_SKILL_MD,
        assets_manifest=[],
        version=NOTE_AI_DIFF_SKILL_VERSION,
    )
