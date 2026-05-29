<script setup lang="ts">
// Phase 5 of the run-detail layout proposal: a slide-in drawer for the
// full tool-call args/result. Triggered from `ToolCallCard.vue`'s
// "View full" button via an injected `openToolDetail` callback (the
// provider is `RunRightPane.vue`, which owns the open/payload state).
//
// Layout: full-height fixed panel anchored to the right edge of the
// viewport, 50vw wide on desktop. Teleported to <body> so transforms /
// overflow on ancestor panes can't break stacking. ARIA dialog with a
// focus trap (composables/useFocusTrap) and Escape / backdrop-click
// close.
//
// Body: a [Code | Markdown | Diff] mode dropdown switches between the
// existing render components in @/components/files/. The drawer
// composes them — it does NOT re-implement rendering.

import { computed, ref, watch } from 'vue'
import CodeRender from '@/components/files/CodeRender.vue'
import MarkdownRender from '@/components/files/MarkdownRender.vue'
import DiffRender from '@/components/files/DiffRender.vue'
import { useFocusTrap } from '@/composables/useFocusTrap'

export interface ToolCallDrawerPayload {
  name: string
  args: unknown
  result: unknown
  isError: boolean
  durationMs: number | null
}

const props = defineProps<{
  open: boolean
  payload: ToolCallDrawerPayload | null
}>()

const emit = defineEmits<{
  close: []
}>()

type RenderMode = 'code' | 'markdown' | 'diff'

const mode = ref<RenderMode>('code')

// Reset mode each time the drawer re-opens, so a previously-selected
// Markdown / Diff mode doesn't carry across into an unrelated tool
// call. Watch `open` (not `payload`) so the operator can browse two
// tool calls in a row without re-selecting Code each time within the
// same opened drawer — but a fresh open starts clean.
watch(
  () => props.open,
  (now) => {
    if (now) mode.value = 'code'
  },
)

function stringify(v: unknown): string {
  if (v === undefined || v === null) return ''
  if (typeof v === 'string') return v
  try {
    return JSON.stringify(v, null, 2)
  } catch {
    return String(v)
  }
}

const argsText = computed(() => stringify(props.payload?.args))
const resultText = computed(() => stringify(props.payload?.result))

// Code-mode language for args is JSON when the underlying value was
// structured; otherwise plain text. For results we default to plain
// text — tool results have no reliable language signal.
function inferLang(v: unknown): string {
  if (v === undefined || v === null) return 'text'
  if (typeof v === 'string') return 'text'
  return 'json'
}

const argsLang = computed(() => inferLang(props.payload?.args))
const resultLang = computed(() => inferLang(props.payload?.result))

// Diff-mode source: the only tool shape we can confidently diff is the
// edit-tool family (args carrying `old_string` + `new_string`). For
// anything else, we surface an explicit empty state in Diff mode rather
// than guessing.
interface DiffSource {
  oldText: string
  newText: string
  filename: string
}

function extractDiffSource(args: unknown): DiffSource | null {
  if (args == null || typeof args !== 'object') return null
  const a = args as Record<string, unknown>
  const oldStr =
    typeof a.old_string === 'string'
      ? a.old_string
      : typeof a.oldText === 'string'
        ? a.oldText
        : null
  const newStr =
    typeof a.new_string === 'string'
      ? a.new_string
      : typeof a.newText === 'string'
        ? a.newText
        : null
  if (oldStr == null || newStr == null) return null
  const filename =
    typeof a.file_path === 'string' && a.file_path !== ''
      ? a.file_path
      : typeof a.path === 'string' && a.path !== ''
        ? a.path
        : 'change'
  return { oldText: oldStr, newText: newStr, filename }
}

const diffSource = computed(() => extractDiffSource(props.payload?.args))

const rootEl = ref<HTMLElement | null>(null)
const activeTrap = computed(() => props.open && props.payload != null)

useFocusTrap(rootEl, {
  active: activeTrap,
  onEscape: () => emit('close'),
})

function onBackdropClick(): void {
  emit('close')
}

function onCloseClick(): void {
  emit('close')
}
</script>

<template>
  <Teleport to="body">
    <div
      v-if="open && payload"
      class="tool-drawer__backdrop"
      data-testid="tool-drawer-backdrop"
      @click="onBackdropClick"
    >
      <section
        ref="rootEl"
        class="tool-drawer"
        role="dialog"
        aria-modal="true"
        :aria-label="`Tool call ${payload.name}`"
        tabindex="-1"
        data-testid="tool-drawer"
        @click.stop
      >
        <header class="tool-drawer__head">
          <div class="tool-drawer__title">
            <span class="tool-drawer__name">{{ payload.name }}</span>
            <span
              v-if="payload.isError"
              class="tool-drawer__badge"
            >error</span>
            <span
              v-if="payload.durationMs != null"
              class="tool-drawer__meta"
            >{{ payload.durationMs }}ms</span>
          </div>
          <div class="tool-drawer__controls">
            <label class="tool-drawer__mode">
              <span class="tool-drawer__mode-label">View as</span>
              <select
                v-model="mode"
                data-testid="tool-drawer-mode"
                class="tool-drawer__mode-select"
              >
                <option value="code">
                  Code
                </option>
                <option value="markdown">
                  Markdown
                </option>
                <option value="diff">
                  Diff
                </option>
              </select>
            </label>
            <button
              type="button"
              class="tool-drawer__close"
              data-testid="tool-drawer-close"
              aria-label="Close drawer"
              @click="onCloseClick"
            >
              ×
            </button>
          </div>
        </header>

        <div class="tool-drawer__body">
          <template v-if="mode === 'code'">
            <section class="tool-drawer__section">
              <h2 class="tool-drawer__section-title">
                args
              </h2>
              <CodeRender
                :source="argsText"
                :lang="argsLang"
              />
            </section>
            <section
              v-if="payload.result !== undefined"
              class="tool-drawer__section"
            >
              <h2 class="tool-drawer__section-title">
                result
              </h2>
              <CodeRender
                :source="resultText"
                :lang="resultLang"
              />
            </section>
          </template>

          <template v-else-if="mode === 'markdown'">
            <section class="tool-drawer__section">
              <h2 class="tool-drawer__section-title">
                args
              </h2>
              <MarkdownRender :source="argsText" />
            </section>
            <section
              v-if="payload.result !== undefined"
              class="tool-drawer__section"
            >
              <h2 class="tool-drawer__section-title">
                result
              </h2>
              <MarkdownRender :source="resultText" />
            </section>
          </template>

          <template v-else-if="mode === 'diff'">
            <section class="tool-drawer__section">
              <h2 class="tool-drawer__section-title">
                diff
              </h2>
              <DiffRender
                v-if="diffSource"
                :old-text="diffSource.oldText"
                :new-text="diffSource.newText"
                :filename="diffSource.filename"
              />
              <p
                v-else
                class="tool-drawer__empty"
                data-testid="tool-drawer-diff-empty"
              >
                Diff not applicable for this tool — no
                <code>old_string</code> / <code>new_string</code>
                pair in args.
              </p>
            </section>
          </template>
        </div>
      </section>
    </div>
  </Teleport>
</template>

<style scoped>
.tool-drawer__backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.4);
  z-index: 50;
  display: flex;
  justify-content: flex-end;
  /* The drawer animates in from the right. Backdrop fades alongside
     it via the same enter transition (instant for now — no fade
     transition is set up at the backdrop level because Teleport's
     enter happens on mount; a fade would need <Transition>). */
}

.tool-drawer {
  width: 50vw;
  min-width: 320px;
  max-width: 100vw;
  height: 100vh;
  background: var(--color-surface);
  border-left: 1px solid var(--color-border);
  box-shadow: -4px 0 12px var(--color-shadow);
  display: flex;
  flex-direction: column;
  /* CSS slide-in: the panel translates from 100% (off-screen right)
     to 0. Honour prefers-reduced-motion. */
  animation: tool-drawer-in 160ms ease-out;
}

@keyframes tool-drawer-in {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

@media (prefers-reduced-motion: reduce) {
  .tool-drawer {
    animation: none;
  }
}

.tool-drawer:focus-visible {
  outline: none;
}

.tool-drawer__head {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--color-border);
}

.tool-drawer__title {
  display: flex;
  align-items: center;
  gap: 0.6rem;
  flex: 1;
  min-width: 0;
}

.tool-drawer__name {
  font-family: var(--font-mono);
  font-weight: 600;
  font-size: 1rem;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.tool-drawer__badge {
  font-size: 0.72em;
  color: var(--color-danger);
  border: 1px solid currentcolor;
  border-radius: 999px;
  padding: 0 0.5em;
}

.tool-drawer__meta {
  font-size: 0.8em;
  color: var(--color-text-dim);
}

.tool-drawer__controls {
  display: flex;
  align-items: center;
  gap: 0.5rem;
}

.tool-drawer__mode {
  display: inline-flex;
  align-items: center;
  gap: 0.4rem;
  font-size: 0.85em;
  color: var(--color-text-dim);
}

.tool-drawer__mode-label {
  text-transform: uppercase;
  font-size: 0.72em;
  letter-spacing: 0.04em;
}

.tool-drawer__mode-select {
  background: var(--color-bg);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  border-radius: 4px;
  font: inherit;
  font-size: 0.9em;
  padding: 0.15rem 0.35rem;
}

.tool-drawer__close {
  width: 2rem;
  height: 2rem;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: transparent;
  border: 1px solid transparent;
  border-radius: 4px;
  color: var(--color-text-dim);
  font: inherit;
  font-size: 1.3rem;
  line-height: 1;
  cursor: pointer;
}

.tool-drawer__close:hover,
.tool-drawer__close:focus-visible {
  color: var(--color-text);
  border-color: var(--color-border);
  background: var(--color-surface-hover);
}

.tool-drawer__body {
  flex: 1;
  overflow-y: auto;
  padding: 0.75rem 1rem 1.5rem;
  display: flex;
  flex-direction: column;
  gap: 1rem;
}

.tool-drawer__section {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.tool-drawer__section-title {
  margin: 0;
  font-size: 0.72em;
  text-transform: uppercase;
  letter-spacing: 0.04em;
  color: var(--color-text-dim);
  font-weight: 600;
}

.tool-drawer__empty {
  margin: 0;
  font-size: 0.9em;
  color: var(--color-text-dim);
}
</style>
