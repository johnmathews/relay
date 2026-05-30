# Chat mode — collapse tool-use cards to one-line previews

**Date:** 2026-05-30
**Scope:** frontend only (`ChatTranscript.vue`, `TimelinePane.vue`, new
`lib/toolPreview.ts`).

## Problem

In chat mode (W4–W6, 2026-05-29 → 2026-05-30) tool calls inside an
assistant turn rendered with the full `ToolCallCard` embedded — name +
`ARGS` JSON block + `RESULT` JSON block + "Show full" toggle. For
read-heavy turns (e.g. an early-Phase-1 exploration that reads 8–10
files) the JSON dwarfed the assistant's prose. The screenshots showed
the user asking a one-sentence question and getting ~10 screens of JSON
before the actual answer. Chat surface should privilege the
conversational thread; tool detail should be on-demand.

## Decision

Tool segments collapse to one line by default:

    ▸ Read · ← /Users/.../project_overview.md · 19ms

The chevron + name + per-tool one-line preview (`$ cmd` for bash, `←
path` for read, `→ path` for write/edit, `? pattern` for grep, etc.) +
duration is the entire row when collapsed. The full `ToolCallCard` only
mounts when expanded.

Auto-expand rule for the live case: a tool with no `tool_use_end` yet
AND which is the latest tool in an open assistant turn renders
expanded with a `running…` chip. The moment a newer tool starts (or
the iter ends) it collapses back unless the operator explicitly
toggled. This preserves the "what is pi doing right now" signal
without leaving every stale completed tool expanded.

User explicitly chose:
1. **Per-tool collapse only** — no group-burst anchoring like the
   timeline does. Adjacent tool collapsing was rejected as overkill
   for the conversational surface.
2. **Auto-expand active tool** — over "always collapsed".

## Mechanism

1. **New shared helper** — `frontend/src/lib/toolPreview.ts` exports
   `toolPreview(name, args)` + `truncatePreview(s)`. Extracted the
   per-tool preview logic from `TimelinePane.previewFor()` (which
   previously had it inline). `TimelinePane` now imports from the
   shared module — one source of truth for the `$ cmd` / `← path` /
   `→ path` / `? pattern` / `* pattern` / description conventions.

2. **`ChatTranscript.vue`** — new `toolOverrides: Ref<Map<string,
   boolean>>` keyed by `seg.key` (`e${event.seq}` of the originating
   `tool_use_start`). Absent key → use default; present key → use
   override. The default = `toolIsLiveLatest(turn, segIdx)`: open
   turn, no result yet, no later tool segments in the same turn.

3. **Template** — the segment renders a `<button
   class="chat-assistant__tool-head">` (the whole one-line header is
   the click target) with `aria-expanded`. `ToolCallCard` is wrapped
   in `v-if="isToolExpanded(...)"` so collapsed segs don't pay the
   render cost.

4. **Styles** — collapsed header is flush against the accent border;
   expanded variant adds padding around the embedded card. Hover
   tint on the header makes it read as a click target.

## Tests

- `tests/toolPreview.spec.ts` (new, 9 cases) — covers per-tool
  format conventions, fallback to first arg key, empty input, and
  140-char truncation.
- `tests/ChatTranscript.spec.ts` — existing "interleaves tool
  calls" case rewritten to assert collapsed-by-default + expand on
  click. Two new cases:
  1. `auto-expands an in-flight tool (no tool_use_end yet) in an
     open turn` — verifies the `running…` chip + expanded state.
  2. `collapses a prior in-flight tool once a newer tool starts` —
     verifies the "latest in turn" rule promotes the new tool and
     demotes the prior one.

Full frontend gate green: eslint + vue-tsc + 479 vitest tests.
Backend untouched but verified: ruff + mypy strict + 398 pytest.

## Files touched

- `frontend/src/lib/toolPreview.ts` (new, 53 LOC)
- `frontend/tests/toolPreview.spec.ts` (new, 56 LOC)
- `frontend/src/components/chat/ChatTranscript.vue` — new
  expand-state plumbing + clickable header + collapsed default;
  styles for `--expanded`, `--chevron`, `--preview`,
  `--meta--pending` modifiers.
- `frontend/src/components/runs/TimelinePane.vue` — replaced the
  inline tool-preview branch in `previewFor()` with a call to the
  shared helper. Removed the now-redundant `truncatePreview` /
  `PREVIEW_MAX` locals.
- `frontend/tests/ChatTranscript.spec.ts` — rewrote 1 case, added 2.
- `docs/dashboard.md` — chat-mode section now describes the
  collapsed default + auto-expand rule.

## Notes for future readers

The chat surface deliberately diverges from the timeline:

- Timeline groups adjacent tool bursts (`▶ 50 tool calls · bash,
  read, edit` anchor) — chat does NOT. Conversational reading
  doesn't benefit from group collapse, and a group anchor would
  hide the per-tool preview that's the whole point.
- Timeline's collapsed row is always one line regardless of tool
  state (it has a separate chip-row filter for visibility) — chat's
  collapse defers to a runtime auto-expand for the live tool only.

The shared `toolPreview` helper is the single source of truth for
the `$ cmd` / `← path` / `→ path` conventions. If a new tool
type warrants a custom preview glyph, add the branch there and both
surfaces pick it up.
