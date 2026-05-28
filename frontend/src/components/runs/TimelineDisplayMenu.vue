<script setup lang="ts">
// "Display" menu — the popover that controls which timeline row types
// expand by default (assistant / thinking / tool / signal / generic).
// State lives in the global timelinePrefs Pinia store, so the button
// can be mounted anywhere — the timeline reads the same store.
//
// Mounted by RunRightPane next to the cancel / action row so it has a
// proper anchor in the page chrome instead of floating over the
// scroll container. Closes on outside-click and Escape.

import { onBeforeUnmount, onMounted, ref } from 'vue'
import {
  useTimelinePrefsStore,
  type TimelineRowType,
} from '@/stores/timelinePrefs'

const prefs = useTimelinePrefsStore()
const open = ref(false)
const rootEl = ref<HTMLElement | null>(null)

const ORDER: readonly TimelineRowType[] = [
  'assistant',
  'thinking',
  'tool',
  'signal',
  'generic',
] as const

const LABEL: Record<TimelineRowType, string> = {
  assistant: 'Assistant',
  thinking: 'Thinking',
  tool: 'Tool calls',
  signal: 'Signals',
  generic: 'Other',
}

function toggle(): void {
  open.value = !open.value
}

function onDocClick(ev: MouseEvent): void {
  if (!open.value) return
  const target = ev.target
  if (!(target instanceof Node)) return
  if (rootEl.value?.contains(target)) return
  open.value = false
}

function onEsc(ev: KeyboardEvent): void {
  if (ev.key === 'Escape') open.value = false
}

onMounted(() => {
  document.addEventListener('click', onDocClick)
  document.addEventListener('keydown', onEsc)
})

onBeforeUnmount(() => {
  document.removeEventListener('click', onDocClick)
  document.removeEventListener('keydown', onEsc)
})
</script>

<template>
  <div
    ref="rootEl"
    class="display-menu"
  >
    <button
      type="button"
      class="display-menu__button"
      :class="{ 'display-menu__button--open': open }"
      data-testid="display-gear"
      aria-haspopup="menu"
      :aria-expanded="open"
      title="Choose which row types are expanded by default"
      @click.stop="toggle"
    >
      <span
        class="display-menu__glyph"
        aria-hidden="true"
      >⚙</span>
      <span class="display-menu__label">Display</span>
      <span
        class="display-menu__chevron"
        :class="{ 'display-menu__chevron--open': open }"
        aria-hidden="true"
      >▾</span>
    </button>
    <div
      v-if="open"
      class="display-menu__popover"
      data-testid="display-popover"
      @click.stop
    >
      <p class="display-menu__title">
        Expand by default
      </p>
      <ul class="display-menu__list">
        <li
          v-for="t in ORDER"
          :key="t"
        >
          <button
            type="button"
            class="display-menu__toggle"
            :class="{ 'is-on': prefs.isExpandedByDefault(t) }"
            :data-testid="`display-toggle-${t}`"
            @click="prefs.toggle(t)"
          >
            <span
              class="display-menu__dot"
              aria-hidden="true"
            />
            {{ LABEL[t] }}
          </button>
        </li>
      </ul>
      <button
        type="button"
        class="display-menu__reset"
        data-testid="display-reset"
        @click="prefs.reset"
      >
        Reset to defaults
      </button>
    </div>
  </div>
</template>

<style scoped>
.display-menu {
  position: relative;
  display: inline-block;
}

.display-menu__button {
  display: inline-flex;
  align-items: center;
  gap: 0.5em;
  background: var(--color-surface);
  border: 1px solid var(--color-border-strong);
  border-radius: 6px;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.85em;
  font-weight: 500;
  line-height: 1;
  padding: 0.45em 0.55em 0.45em 0.7em;
}

.display-menu__button:hover,
.display-menu__button:focus-visible {
  border-color: var(--color-text-dim);
  background: var(--color-surface-hover);
  outline: none;
}

.display-menu__button--open {
  background: var(--color-surface-hover);
  border-color: var(--color-text-dim);
}

.display-menu__glyph {
  font-size: 0.95em;
  line-height: 1;
  color: var(--color-text-dim);
}

/* Chevron — the clearest "this is a dropdown" affordance. Rotates 180°
   when the popover is open so the cue stays correct in both states.
   Pattern: GitHub filter pills, Linear's Display button, Tailwind UI
   menu button. */
.display-menu__chevron {
  display: inline-block;
  font-size: 0.78em;
  line-height: 1;
  color: var(--color-text-dim);
  margin-left: 0.15em;
  transition: transform 0.15s ease;
}

.display-menu__chevron--open {
  transform: rotate(180deg);
}

.display-menu__popover {
  position: absolute;
  top: calc(100% + 0.35rem);
  right: 0;
  min-width: 13rem;
  padding: 0.6rem;
  background: var(--color-surface);
  border: 1px solid var(--color-border);
  border-radius: 8px;
  box-shadow: 0 6px 18px var(--color-shadow);
  z-index: 10;
}

.display-menu__title {
  margin: 0 0 0.4rem;
  font-size: 0.72em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-dim);
}

.display-menu__list {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.15rem;
}

.display-menu__toggle {
  display: flex;
  align-items: center;
  gap: 0.55rem;
  width: 100%;
  background: none;
  border: 1px solid transparent;
  border-radius: 4px;
  color: var(--color-text);
  cursor: pointer;
  font: inherit;
  font-size: 0.88em;
  padding: 0.3em 0.4em;
  text-align: left;
}

.display-menu__toggle:hover {
  background: var(--color-surface-hover);
}

.display-menu__dot {
  display: inline-block;
  width: 0.6em;
  height: 0.6em;
  border-radius: 50%;
  border: 1px solid var(--color-text-dim);
}

.display-menu__toggle.is-on .display-menu__dot {
  background: var(--color-accent);
  border-color: var(--color-accent);
}

.display-menu__reset {
  margin-top: 0.5rem;
  background: none;
  border: none;
  color: var(--color-text-dim);
  cursor: pointer;
  font: inherit;
  font-size: 0.78em;
  padding: 0;
  text-decoration: underline;
}

.display-menu__reset:hover {
  color: var(--color-text);
}
</style>
