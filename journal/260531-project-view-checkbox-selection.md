# 260531 — Always-visible checkboxes + Chats bulk delete

## What

Two UX tweaks to `ProjectView.vue`:

1. **Runs pane** — removed the "Select runs" / "Cancel" toggle. The
   checkbox now renders next to every run row by default, the row's
   body click still navigates to the run detail, and the actions bar
   shows **Select all** + **Delete selected (N)** whenever the project
   has runs. Active runs (`running` / `awaiting_children`) still
   render a disabled checkbox with the "Cancel this run before
   deleting" tooltip.
2. **Chats pane** — added the same affordances. Per-row checkbox
   (`chat-check-<id>`), **Select all** + **Delete selected (N)** bar,
   confirm dialog that explicitly states files on disk aren't removed,
   and an inline error line for partial-failure rejections. Reuses the
   existing `useDeleteRunMutation` since chats are runs with
   `mode='chat'`.

## Why

The user reported the select-mode toggle as bad UX — "having to click
'select runs' first and then select the runs". Two clicks where one
would do. The chats pane previously had no delete affordance at all,
even though the underlying `DELETE /api/runs/{id}` endpoint accepts
both task and chat runs (chats share the runs table — ADR-49 §9, the
visual segregation is dashboard-only). Making chats deletable closes
the gap.

## How it works

- `selectedRunIds` and `selectedChatIds` are kept as **two separate
  refs**, not merged into one. Same for the `confirm` flags and the
  `lastDeleteSummary` shapes. The two lists deliberately don't share
  selection — a selection in Runs shouldn't bleed into Chats when the
  operator switches tabs, and the confirm dialogs need to render
  list-specific copy ("1 run" vs "1 chat"). The handlers are
  near-mirrors; the duplication is intentional per the project's
  "three similar lines is better than a premature abstraction" rule.
- Row click → `openRun` / `openChat` (always navigates). Checkbox
  click → `toggleRunSelection` / `toggleChatSelection` with
  `.stop` to keep the click from bubbling into the row's button.
- `ACTIVE_STATUSES` (the `running` / `awaiting_children` guard set)
  is shared between both panes since the deletion semantics are
  identical regardless of `mode`.
- The `.project-view__run-wrap--selected` CSS class now also styles
  `.project-view__chat` so the selected highlight works on both list
  variants without a new class.

## Tests

`frontend/tests/ProjectView.spec.ts`:
- Rewrote the two runs multi-select tests to assert that checkboxes
  are visible **without** entering a select mode and that row click
  navigates while checkbox click toggles selection without navigating.
- Added two new tests covering chat bulk delete (asserts the running
  chat's checkbox is disabled, the two terminal chats' IDs flow into
  the `useDeleteRunMutation` mock) and checkbox-without-navigation.

481/481 passing. Lint + typecheck clean.

## Docs

`docs/dashboard.md` "Runs pane — multi-select delete" was rewritten
into "Runs and Chats panes — multi-select delete" to drop the
no-longer-accurate "Select runs toggle" language and document the
chats parallel.

## Files

```
frontend/src/views/ProjectView.vue
frontend/tests/ProjectView.spec.ts
docs/dashboard.md
```
