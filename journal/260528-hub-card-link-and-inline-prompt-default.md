# 260528 — hub: whole-card click; new-run scoped to a project; inline prompt is the wizard default (ADR-47)

Three small dashboard UX adjustments raised during acceptance use,
shipped together because they all clean up the same fanned-out
affordance:

1. **Hub project cards are now a single click target.** `<article>`
   wrapped in a per-tile action row (Open / New run) → a single
   `<RouterLink>` whose content *is* the card. Hover is the accent
   border; focus-visible adds a 2px outline. No more guessing whether
   "Open" or the card body does the same thing.
2. **"New run" is no longer a hub affordance.** It was always a
   project-scoped action (the wizard's first ref is the
   `projectId`), and the Project view already has a "New run" button
   for the just-selected project. Removing it from the hub
   collapses one whole row per tile and removes the only path that
   pretended you could start a run without entering a project first.
3. **Wizard step-1 default is "Write one inline".** Most projects
   have no saved prompts yet, so the old "Use a saved prompt"
   default put the empty-state text in front of the user, who then
   had to click the second radio to get to the textarea. Reordered
   the radios so inline appears first and is the default; the saved-
   prompt list is the secondary option. Initial `Next` is still
   disabled until the textarea has content — the `hasPrompt` gate
   is unchanged.

## Trap from the test pass

`HubView.spec.ts` was stubbing `RouterLink: true` (boolean stub,
which strips children). Worked when the card was an `<article>`
with sibling RouterLink children, broke the moment the entire card
became a `<RouterLink>` — the stub swallowed the slot and the
"project name appears" assertion failed because the rendered text
was just "Projects New project" (the hub header). Switched the stub
to `RouterLinkStub` from `@vue/test-utils`, which renders the slot.
Worth remembering: `stubs: { RouterLink: true }` is fine for the
hub's own header link, but the moment you wrap a card body in a
RouterLink the stub strategy has to change.

## CSS-var nit caught on review

First pass used
`background: var(--color-surface-hover, var(--color-surface))` on
hover. `--color-surface-hover` isn't defined anywhere in
`styles/base.css`, so the fallback collapsed to the same surface
colour as the resting state — a hover *background* change that
never changed anything. Dropped the bg rule entirely; the border
colour swap is the actual affordance.

## Tests

`NewRunWizard.spec.ts` had nine tests that started with
`get('input[name="existing-prompt"][value="11"]').setValue()`. With
the wizard defaulting to inline mode now, the existing-prompt
radios aren't even rendered until the user switches modes. Did a
`replace_all` Edit prepending
`get('input[value="existing"]').setValue()` to each, plus split the
"renders step 1" test into two: one asserts inline-default
(textarea visible, prompt-list NOT visible, Next disabled until
typing); the other asserts switching to saved mode reveals the
prompt list. Full gate (`npm run check`: eslint + vue-tsc +
vitest) green — 234/234.

## What I didn't change

The Project view's "New run" button is untouched — that's where
the wizard is reached from now and it already worked. The wizard's
step-1 inline preview is still the deliberately minimal `<pre>`
render (W6 owns the full markdown pipeline, comment in
`StepPromptSelect.vue` is unchanged). No backend touched.

## Docs

- `docs/spec.md` §9.1: hub now has one top-level action ("Register
  project"), each card is a single link, wizard prompt step defaults
  to inline.
- `docs/decisions.md`: new ADR-47 records the three decisions, the
  alternatives (dynamic default mode, keeping both per-card
  actions) and why they were rejected, and the concrete change set.
- `CLAUDE.md`: ADR count 46 → 47.

## Files

```
frontend/src/components/projects/ProjectCard.vue
frontend/src/components/runs/wizard/StepPromptSelect.vue
frontend/src/views/NewRunWizard.vue
frontend/tests/HubView.spec.ts
frontend/tests/NewRunWizard.spec.ts
frontend/tests/ProjectList.spec.ts
docs/spec.md
docs/decisions.md
CLAUDE.md
```
