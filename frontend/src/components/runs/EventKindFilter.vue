<script setup lang="ts">
// Persistent chip row above the timeline. Five toggles (Assistant /
// Thinking / Tool calls / Signals / Other) — clicking a chip flips
// that category's **expand-by-default** state in the timelinePrefs
// store. A "lit" chip means rows of that type are expanded out-of-the
// box; a dim chip means they're collapsed and the user has to click a
// row's header to read it. There is no visibility filter — every step
// is always rendered. This is the merge of the previous chip-row
// visibility filter and the Display popover.
//
// The chip dot keeps the per-category colour (matches the card border
// hue shipped in 8180ace) and the count badge still shows how many
// rows of that kind the current scope contains, so the row doubles
// as a colour legend + live activity readout.

import { computed } from 'vue'
import {
  KIND_CATEGORIES,
  KIND_LABEL,
  categoryToRowType,
  type KindCategory,
} from '@/lib/eventKinds'
import { useTimelinePrefsStore } from '@/stores/timelinePrefs'

defineProps<{
  /**
   * Per-category counts IN THE CURRENT SCOPE (cross-iter for the
   * Overview body, iter-scoped for an Iter body). Always shown on the
   * chip so the operator sees "how many rows of this kind exist"
   * regardless of expand state.
   */
  counts: Readonly<Record<KindCategory, number>>
}>()

const prefs = useTimelinePrefsStore()

function isExpanded(k: KindCategory): boolean {
  return prefs.isExpandedByDefault(categoryToRowType(k))
}

function onToggle(k: KindCategory): void {
  prefs.toggle(categoryToRowType(k))
}

function chipTitle(k: KindCategory): string {
  const verb = isExpanded(k) ? 'Collapse' : 'Expand'
  return `${verb} ${KIND_LABEL[k]} steps by default`
}

const hasAnyExpanded = computed(() =>
  KIND_CATEGORIES.some((k) => isExpanded(k)),
)

function onResetDefaults(): void {
  prefs.reset()
}
</script>

<template>
  <div
    class="kind-filter"
    role="toolbar"
    aria-label="Toggle expand-by-default per event kind"
    data-testid="event-kind-filter"
  >
    <button
      v-for="k in KIND_CATEGORIES"
      :key="k"
      type="button"
      class="kind-filter__chip"
      :class="{ 'is-on': isExpanded(k) }"
      :data-kind="k"
      :data-testid="`kind-chip-${k}`"
      :aria-pressed="isExpanded(k)"
      :title="chipTitle(k)"
      @click="onToggle(k)"
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
    <button
      v-if="hasAnyExpanded"
      type="button"
      class="kind-filter__reset"
      data-testid="kind-filter-reset"
      title="Collapse every kind by default"
      @click="onResetDefaults"
    >
      Reset to collapsed
    </button>
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
  transition: background-color 0.12s ease, border-color 0.12s ease;
}

.kind-filter__chip:hover,
.kind-filter__chip:focus-visible {
  border-color: var(--color-border-strong);
  background: var(--color-surface-hover);
  outline: none;
}

/* Lit ("expanded by default") state — chip takes its category's
   pastel background + border so the operator can read at a glance
   which kinds will be opened on first render. Matches the card
   palette tokens shipped in 8180ace. */
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
.kind-filter__chip.is-on[data-kind='other'] {
  background: var(--color-row-other-bg);
  border-color: var(--color-row-other-border);
}

.kind-filter__chip.is-on .kind-filter__label {
  font-weight: 600;
}

.kind-filter__dot {
  display: inline-block;
  width: 0.7em;
  height: 0.7em;
  border-radius: 50%;
  background: var(--color-text-dim);
  border: 1px solid transparent;
}

/* Reuse the per-row palette tokens shipped in 8180ace so the chip
   dot and the card border match. */
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
.kind-filter__dot[data-kind='other'] {
  background: var(--color-row-other-border);
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

.kind-filter__reset {
  margin-left: auto;
  background: none;
  border: none;
  color: var(--color-text-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.78em;
  padding: 0;
  text-decoration: underline;
}

.kind-filter__reset:hover,
.kind-filter__reset:focus-visible {
  color: var(--color-text);
  outline: none;
}
</style>
