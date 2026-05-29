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
   * Display-row index range currently visible in the timeline. The
   * minimap positions both ticks AND the viewport overlay in
   * `i / (n-1)` index space — so a row's tick and the viewport edge
   * that frames it land at the same %. The parent (TimelinePane)
   * translates the scroll container's pixel scroll → display-row
   * range using the fixed `ROW_HEIGHT` math.
   */
  viewportStart: number
  viewportEnd: number
}>()

const emit = defineEmits<{
  /** Display-row index (fractional) the operator clicked / dragged to. */
  (e: 'scroll-to-index', index: number): void
}>()

const strip = ref<HTMLElement | null>(null)
const dragging = ref(false)

/**
 * Slot-based layout: each tick occupies `100 / n %` of the strip,
 * laid out top-to-bottom with no gaps or overlap. Tick `i` covers
 * the band `[i/n, (i+1)/n]`. This makes the strip read as
 * continuous coloured bands instead of isolated notches when the
 * display row count is small relative to the strip's pixel height.
 * Same `1 / n` slot size is used by the viewport overlay below.
 */
function tickStyleFor(i: number): { top: string; height: string } {
  const n = props.ticks.length
  if (n <= 0) return { top: '0%', height: '100%' }
  return { top: `${(i / n) * 100}%`, height: `${100 / n}%` }
}

/**
 * The viewport overlay. Frames the slots of rows in
 * `[viewportStart, viewportEnd]` inclusive — so a viewport of `N`
 * rows covers `N / total` of the strip. With 0 ticks the overlay
 * collapses; the `v-if` on the parent already hides the strip in
 * that case but we stay defensive.
 */
const viewportStyle = computed(() => {
  const n = props.ticks.length
  if (n <= 0) return { top: '0%', height: '100%' }
  const startSlot = Math.max(0, Math.min(n - 1, props.viewportStart))
  const endSlot = Math.max(startSlot, Math.min(n - 1, props.viewportEnd))
  const top = (startSlot / n) * 100
  const height = Math.max(100 / n, ((endSlot - startSlot + 1) / n) * 100)
  return { top: `${top}%`, height: `${height}%` }
})

function indexFromEvent(ev: PointerEvent): number {
  const el = strip.value
  const n = props.ticks.length
  if (el == null || n <= 0) return 0
  const rect = el.getBoundingClientRect()
  const y = ev.clientY - rect.top
  const ratio = rect.height > 0 ? y / rect.height : 0
  // Convert click ratio → slot index. Floor so a click in the
  // middle of slot k targets row k, not k+0.5 (which `Math.round`
  // would push to k+1 for any click past the slot's midpoint).
  return Math.max(0, Math.min(n - 1, Math.floor(ratio * n)))
}

function onPointerDown(ev: PointerEvent): void {
  dragging.value = true
  ;(ev.currentTarget as HTMLElement).setPointerCapture(ev.pointerId)
  emit('scroll-to-index', indexFromEvent(ev))
}

function onPointerMove(ev: PointerEvent): void {
  if (!dragging.value) return
  emit('scroll-to-index', indexFromEvent(ev))
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
  /* `top` + `height` set inline by tickStyleFor — each tick fills
     its slot (`100 / n %` of the strip) so consecutive ticks tile
     edge-to-edge without gaps. No border-radius — squared edges
     read as continuous coloured bands. */
  background: var(--color-text-dim);
  pointer-events: none;
}

/* Per-type tick colour — reuses the row-bg tokens directly so each
   tick is pixel-identical to its matching timeline row (both compose
   over --color-surface). Tokens in styles/base.css. */
.minimap__tick[data-row-type='assistant'] {
  background: var(--color-row-assistant-bg);
}
.minimap__tick[data-row-type='thinking'] {
  background: var(--color-row-thinking-bg);
}
.minimap__tick[data-row-type='tool'] {
  background: var(--color-row-tool-bg);
}
.minimap__tick[data-row-type='signal'] {
  background: var(--color-row-signal-bg);
}
.minimap__tick[data-row-type='boundary'],
.minimap__tick[data-row-type='generic'],
.minimap__tick[data-row-type='usage'] {
  background: var(--color-row-other-bg);
}
.minimap__tick[data-row-type='artifact_edited'],
.minimap__tick[data-row-type='pause'] {
  background: var(--color-warning-bg);
}

/* Viewport indicator — a transparent rectangle (no fill) bordered on
   all four sides so the strip's coloured bands remain visible
   *inside* the current viewport while the frame reads clearly as a
   contained region. */
.minimap__viewport {
  position: absolute;
  left: 0;
  right: 0;
  background: transparent;
  border: 2px solid var(--color-accent);
  pointer-events: none;
  transition: top 60ms linear, height 60ms linear;
}
</style>
