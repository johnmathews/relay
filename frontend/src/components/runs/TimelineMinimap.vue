<script setup lang="ts">
// VS Code-style overview of the timeline: a thin vertical strip
// showing every event as a coloured tick. Lets the operator see the
// SHAPE of a long run at a glance — bands of teal mark tool bursts,
// purple the thinking phases, blue the assistant replies, green the
// signals, slate the structural events. The current viewport is
// marked by a translucent overlay; clicking/dragging the strip
// scrolls the underlying timeline.
//
// Layout: positioned absolutely inside its parent (TimelinePane), so
// the strip fills the timeline scroll container's height exactly.
// The parent owns the scroll state — this component is pure
// rendering + emits a `scrollTo(px)` event the parent applies.

import { computed, ref } from 'vue'

interface MinimapTick {
  type: string
  /** Index into the rows array — used to position the tick. */
  index: number
}

const props = defineProps<{
  /** Total number of rows in the timeline (pre-grouping). */
  ticks: ReadonlyArray<MinimapTick>
  /**
   * The scroll container's geometry: clientHeight (viewport) and
   * scrollHeight (total content) drive the viewport overlay's height
   * and position. scrollTop is the current top of the viewport.
   */
  scrollTop: number
  viewportH: number
  scrollHeight: number
}>()

const emit = defineEmits<{
  (e: 'scroll-to', px: number): void
}>()

const strip = ref<HTMLElement | null>(null)
const dragging = ref(false)

/**
 * Map a row index → percentage along the strip. Linear; assumes
 * approximately uniform row heights (true under virtualisation,
 * approximate otherwise — minor drift doesn't matter for a shape
 * indicator). The denominator is `n - 1` (not `n`) so the LAST
 * tick lands at 100% rather than `(n - 1) / n * 100%` — without
 * this correction a 5-tick minimap leaves an empty 20% band at
 * the bottom of the strip. With only one tick `(n - 1) === 0`, so
 * we centre it at 0% (start-of-strip).
 */
function tickStyleFor(i: number): { top: string } {
  const n = props.ticks.length
  if (n <= 1) return { top: '0%' }
  return { top: `${(i / (n - 1)) * 100}%` }
}

/**
 * The translucent viewport overlay. Position + height come from the
 * scroller's geometry mapped onto the strip's height (assumed equal
 * since the strip is positioned to fill the same container). When
 * scrollHeight is 0 or smaller than the viewport (no scroll possible)
 * the overlay covers the whole strip — accurate.
 */
const viewportStyle = computed(() => {
  const total = props.scrollHeight
  if (total <= 0) return { top: '0%', height: '100%' }
  const top = Math.max(0, Math.min(100, (props.scrollTop / total) * 100))
  const height = Math.max(
    4,
    Math.min(100, (props.viewportH / total) * 100),
  )
  return { top: `${top}%`, height: `${height}%` }
})

function pxFromEvent(ev: PointerEvent): number {
  const el = strip.value
  if (el == null) return 0
  const rect = el.getBoundingClientRect()
  const y = ev.clientY - rect.top
  const ratio = rect.height > 0 ? y / rect.height : 0
  // Centre the clicked position in the viewport, clamping at 0 / max.
  const target = ratio * props.scrollHeight - props.viewportH / 2
  return Math.max(0, target)
}

function onPointerDown(ev: PointerEvent): void {
  dragging.value = true
  ;(ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId)
  emit('scroll-to', pxFromEvent(ev))
}

function onPointerMove(ev: PointerEvent): void {
  if (!dragging.value) return
  emit('scroll-to', pxFromEvent(ev))
}

function onPointerUp(ev: PointerEvent): void {
  dragging.value = false
  ;(ev.currentTarget as HTMLElement).releasePointerCapture(ev.pointerId)
}
</script>

<template>
  <!-- The minimap is a visual shape-of-the-document indicator
       + pointer-driven scroll shortcut. It deliberately carries
       no ARIA role: keyboard users access the timeline via the
       focusable scroll container itself, and the coloured-band
       pattern is meaningless without sight. aria-hidden keeps it
       out of the accessibility tree so AT users see one canonical
       scroll surface instead of two competing ones. -->
  <div
    ref="strip"
    class="minimap"
    aria-hidden="true"
    data-testid="timeline-minimap"
    @pointerdown="onPointerDown"
    @pointermove="onPointerMove"
    @pointerup="onPointerUp"
    @pointercancel="onPointerUp"
  >
    <span
      v-for="(t, i) in ticks"
      :key="i"
      class="minimap__tick"
      :data-row-type="t.type"
      :style="tickStyleFor(i)"
    />
    <span
      class="minimap__viewport"
      :style="viewportStyle"
      data-testid="minimap-viewport"
    />
  </div>
</template>

<style scoped>
.minimap {
  position: relative;
  width: 24px;
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: 6px;
  overflow: hidden;
  cursor: pointer;
  user-select: none;
  touch-action: none;
}

.minimap:hover,
.minimap:focus-visible {
  border-color: var(--color-accent);
  outline: none;
}

.minimap__tick {
  position: absolute;
  left: 1px;
  right: 1px;
  height: 4px;
  border-radius: 1px;
  background: var(--color-text-dim);
  /* Taller ticks (4px) overlap on dense runs to form continuous
     coloured bands; sparse runs still read as distinct stripes.
     Translate so the band is centred on the row's position. */
  transform: translateY(-2px);
  pointer-events: none;
}

/* Per-type tick colour — uses the dedicated minimap palette, not the
   card borders. On dark surface the minimap tokens equal the border
   colours (already bright pastels). In light theme they drop to the
   300-band so a dense run reads as friendly pastel bands instead of
   a slab of dark border colour. Tokens in styles/base.css. */
.minimap__tick[data-row-type='assistant'] {
  background: var(--color-row-assistant-minimap);
}
.minimap__tick[data-row-type='thinking'] {
  background: var(--color-row-thinking-minimap);
}
.minimap__tick[data-row-type='tool'] {
  background: var(--color-row-tool-minimap);
}
.minimap__tick[data-row-type='signal'] {
  background: var(--color-row-signal-minimap);
}
.minimap__tick[data-row-type='boundary'],
.minimap__tick[data-row-type='generic'],
.minimap__tick[data-row-type='usage'] {
  background: var(--color-row-other-minimap);
}
.minimap__tick[data-row-type='artifact_edited'],
.minimap__tick[data-row-type='pause'] {
  background: var(--color-row-warning-minimap);
}

.minimap__viewport {
  position: absolute;
  left: 0;
  right: 0;
  background: var(--color-accent-soft-strong);
  border-top: 2px solid var(--color-accent);
  border-bottom: 2px solid var(--color-accent);
  box-shadow: inset 0 0 0 1px var(--color-accent-soft);
  pointer-events: none;
  transition: top 60ms linear, height 60ms linear;
}
</style>
