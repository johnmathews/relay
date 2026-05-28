<script setup lang="ts">
// Persistent chip row above the timeline. Five toggles (Assistant /
// Thinking / Tool calls / Signals / Other) — clicking a chip toggles
// that category's visibility in the timeline below.
//
// The chip row doubles as the legend for the per-card colour palette
// shipped in 8180ace: each chip's dot uses the same
// `--color-row-<kind>-border` token the cards do, so the operator can
// build a one-glance mental map of colour → kind.
//
// State model: visibility is "all on" by default. A `null` v-model
// value means all categories are visible (and the URL has no
// `&kinds=` param — see `lib/eventKinds.ts::serializeKinds`). A
// non-null `Set<KindCategory>` is the proper subset that should
// remain visible. Toggling the last chip off doesn't disappear the
// timeline silently — TimelinePane renders an "all hidden by filter"
// affordance with a Clear button that emits `null`.

import { computed } from 'vue'
import {
  KIND_CATEGORIES,
  KIND_LABEL,
  type KindCategory,
} from '@/lib/eventKinds'

const props = defineProps<{
  /**
   * Allowed categories, or null for "show all". Mirrors the URL
   * `&kinds=` param shape. Parent owns parsing/serialising; this
   * component is a pure UI control.
   */
  modelValue: ReadonlySet<KindCategory> | null
  /**
   * Per-category count IN THE CURRENT SCOPE (cross-iter for the
   * Overview body, iter-scoped for an Iter body). The counts reflect
   * the unfiltered event list — so toggling a chip off shows the
   * operator how many rows they are hiding.
   */
  counts: Readonly<Record<KindCategory, number>>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', value: ReadonlySet<KindCategory> | null): void
}>()

function isVisible(k: KindCategory): boolean {
  if (props.modelValue == null) return true
  return props.modelValue.has(k)
}

/**
 * Toggle a single category. If the result is the empty set we keep
 * the empty set (so the timeline can surface "all hidden" + Clear) —
 * we don't auto-collapse to `null`. If the result is the full set we
 * emit `null` so the URL drops the param entirely.
 */
function onToggle(k: KindCategory): void {
  const current = props.modelValue
  // Compute the next set against the EFFECTIVE current set, which is
  // "all categories" when modelValue is null. That way the first
  // chip click hides exactly one category instead of clearing four.
  const next = new Set<KindCategory>(
    current == null ? KIND_CATEGORIES : current,
  )
  if (next.has(k)) next.delete(k)
  else next.add(k)
  if (next.size === KIND_CATEGORIES.length) {
    emit('update:modelValue', null)
    return
  }
  emit('update:modelValue', next)
}

const hasFilter = computed(() => props.modelValue != null)

function onClear(): void {
  emit('update:modelValue', null)
}
</script>

<template>
  <div
    class="kind-filter"
    role="toolbar"
    aria-label="Filter timeline by event kind"
    data-testid="event-kind-filter"
  >
    <button
      v-for="k in KIND_CATEGORIES"
      :key="k"
      type="button"
      class="kind-filter__chip"
      :class="{ 'is-off': !isVisible(k) }"
      :data-kind="k"
      :data-testid="`kind-chip-${k}`"
      :aria-pressed="isVisible(k)"
      :title="isVisible(k) ? `Hide ${KIND_LABEL[k]}` : `Show ${KIND_LABEL[k]}`"
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
      v-if="hasFilter"
      type="button"
      class="kind-filter__clear"
      data-testid="kind-filter-clear"
      title="Show every kind"
      @click="onClear"
    >
      Clear filter
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
  transition: opacity 0.12s ease, border-color 0.12s ease;
}

.kind-filter__chip:hover,
.kind-filter__chip:focus-visible {
  border-color: var(--color-border-strong);
  background: var(--color-surface-hover);
  outline: none;
}

/* Filtered-off state — chip is still readable (count visible) but
   visibly de-emphasised so the on/off state reads at a glance even
   when colour is not the primary cue. */
.kind-filter__chip.is-off {
  opacity: 0.45;
  text-decoration: line-through;
  text-decoration-thickness: 1px;
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
  font-weight: 500;
  letter-spacing: 0.02em;
}

.kind-filter__count {
  font-variant-numeric: tabular-nums;
  font-size: 0.85em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.kind-filter__clear {
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

.kind-filter__clear:hover,
.kind-filter__clear:focus-visible {
  color: var(--color-text);
  outline: none;
}
</style>
