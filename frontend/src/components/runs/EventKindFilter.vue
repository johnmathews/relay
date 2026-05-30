<script setup lang="ts">
// Persistent chip row above the timeline. Eight toggles (Assistant /
// Thinking / Tool calls / Signals / Boundaries / Pauses / Artifacts /
// Other) drive a focus-style filter against
// `useTimelinePrefsStore`:
//
// - Default state (`mode === 'all'`): every chip lit; everything
//   visible in the timeline.
// - First chip click: enter `subset` mode with just that chip → only
//   that kind visible. Other chips dim.
// - Subsequent clicks: add / remove chips from the active set.
// - Removing the last active chip snaps back to `all` (empty-set
//   recovery — the user can't strand themselves on an empty timeline
//   via chip clicks; that requires the explicit "Show none" button).
// - "Show all" and "Show none" buttons jump directly to those states.
//
// The chip dot keeps the per-category colour (matches the card border
// hue), and the count badge still shows how many rows of that kind
// exist in the current scope so the row doubles as a colour legend +
// live activity readout. Each chip's tooltip lists the underlying
// event kinds in that category.

import {
  KIND_CATEGORIES,
  KIND_LABEL,
  KIND_MEMBERS,
  type KindCategory,
} from '@/lib/eventKinds'
import { useTimelinePrefsStore } from '@/stores/timelinePrefs'

defineProps<{
  /**
   * Per-category counts IN THE CURRENT SCOPE (cross-iter for the
   * Overview body, iter-scoped for an Iter body). Always shown on the
   * chip so the operator sees "how many rows of this kind exist"
   * regardless of visibility.
   */
  counts: Readonly<Record<KindCategory, number>>
}>()

const prefs = useTimelinePrefsStore()

function chipTitle(k: KindCategory): string {
  const verb = prefs.isActive(k) ? 'Hide' : 'Show'
  const members = KIND_MEMBERS[k].join(', ')
  return `${verb} ${KIND_LABEL[k]} steps\n${members}`
}
</script>

<template>
  <div
    class="kind-filter"
    role="toolbar"
    aria-label="Toggle event-kind visibility"
    data-testid="event-kind-filter"
  >
    <button
      v-for="k in KIND_CATEGORIES"
      :key="k"
      type="button"
      class="kind-filter__chip"
      :class="{ 'is-on': prefs.isActive(k) }"
      :data-kind="k"
      :data-testid="`kind-chip-${k}`"
      :aria-pressed="prefs.isActive(k)"
      :title="chipTitle(k)"
      @click="prefs.toggle(k)"
    >
      <span
        class="kind-filter__dot"
        :data-kind="k"
        aria-hidden="true"
      />
      <span class="kind-filter__label">{{ KIND_LABEL[k] }}</span>
      <span
        class="kind-filter__count"
        :data-testid="`kind-count-${k}`"
      >{{ counts[k] }}</span>
    </button>
    <div class="kind-filter__actions">
      <button
        v-if="prefs.mode !== 'all'"
        type="button"
        class="kind-filter__action"
        data-testid="kind-filter-reset"
        title="Show every kind"
        @click="prefs.showAll()"
      >
        Show all
      </button>
      <button
        v-if="prefs.mode !== 'none'"
        type="button"
        class="kind-filter__action"
        data-testid="kind-filter-none"
        title="Hide every kind"
        @click="prefs.showNone()"
      >
        Show none
      </button>
    </div>
  </div>
</template>

<style scoped>
.kind-filter {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 0.4rem;
  padding: 0.45rem 0.6rem;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
}

.kind-filter__chip {
  display: inline-flex;
  align-items: center;
  gap: 0.4em;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 999px;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.82em;
  line-height: 1;
  padding: 0.4em 0.7em;
  min-height: 1.9rem;
  transition: background-color 0.12s ease, border-color 0.12s ease,
    opacity 0.12s ease;
}

.kind-filter__chip:hover,
.kind-filter__chip:focus-visible {
  border-color: var(--color-border-strong);
  background: var(--color-surface-hover);
  outline: none;
}

/* Lit ("active") state — chip takes its category's pastel
   background + border so the operator can read at a glance which
   kinds are showing. A dim (off-state) chip drops opacity on its dot
   + label so it reads as "off". */
.kind-filter__chip.is-on[data-kind='assistant'] {
  background: var(--color-row-assistant-bg);
  border-color: var(--color-row-assistant-border);
}
.kind-filter__chip.is-on[data-kind='thinking'] {
  background: var(--color-row-thinking-bg);
  border-color: var(--color-row-thinking-border);
}
.kind-filter__chip.is-on[data-kind='tool'] {
  background: var(--color-row-tool-bg);
  border-color: var(--color-row-tool-border);
}
.kind-filter__chip.is-on[data-kind='signal'] {
  background: var(--color-row-signal-bg);
  border-color: var(--color-row-signal-border);
}
.kind-filter__chip.is-on[data-kind='boundary'] {
  background: var(--color-row-signal-bg);
  border-color: var(--color-row-signal-border);
}
.kind-filter__chip.is-on[data-kind='pause'] {
  background: var(--color-warning-bg);
  border-color: var(--color-warning);
}
.kind-filter__chip.is-on[data-kind='artifact'] {
  background: var(--color-row-other-bg);
  border-color: var(--color-row-other-border);
}
.kind-filter__chip.is-on[data-kind='other'] {
  background: var(--color-row-other-bg);
  border-color: var(--color-row-other-border);
}

.kind-filter__chip.is-on .kind-filter__label {
  font-weight: 600;
}

/* Dim an inactive chip — keep it readable so the user can still
   toggle it back on, but make the off-state visually obvious. */
.kind-filter__chip:not(.is-on) {
  opacity: 0.55;
}
.kind-filter__chip:not(.is-on):hover,
.kind-filter__chip:not(.is-on):focus-visible {
  opacity: 0.85;
}

.kind-filter__dot {
  display: inline-block;
  width: 0.7em;
  height: 0.7em;
  border-radius: 50%;
  background: var(--color-text-dim);
  border: 1px solid transparent;
}

/* Reuse the per-row palette tokens so the chip dot and the card
   border match. Boundary borrows the signal hue (they're related);
   pause borrows the warning hue; artifact + other share the
   neutral "other" hue. */
.kind-filter__dot[data-kind='assistant'] {
  background: var(--color-row-assistant-border);
}
.kind-filter__dot[data-kind='thinking'] {
  background: var(--color-row-thinking-border);
}
.kind-filter__dot[data-kind='tool'] {
  background: var(--color-row-tool-border);
}
.kind-filter__dot[data-kind='signal'] {
  background: var(--color-row-signal-border);
}
.kind-filter__dot[data-kind='boundary'] {
  background: var(--color-row-signal-border);
  opacity: 0.65;
}
.kind-filter__dot[data-kind='pause'] {
  background: var(--color-warning);
}
.kind-filter__dot[data-kind='artifact'] {
  background: var(--color-row-other-border);
}
.kind-filter__dot[data-kind='other'] {
  background: var(--color-row-other-border);
  opacity: 0.6;
}

.kind-filter__label {
  letter-spacing: 0.02em;
}

.kind-filter__count {
  font-variant-numeric: tabular-nums;
  font-size: 0.85em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.kind-filter__actions {
  display: inline-flex;
  align-items: center;
  gap: 0.6rem;
  margin-left: auto;
}

.kind-filter__action {
  background: none;
  border: none;
  color: var(--color-text-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.78em;
  padding: 0;
  text-decoration: underline;
}

.kind-filter__action:hover,
.kind-filter__action:focus-visible {
  color: var(--color-text);
  outline: none;
}
</style>
