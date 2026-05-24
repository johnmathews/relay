<script setup lang="ts">
import { ref } from 'vue'

interface DirEntry {
  name: string
  path: string
}

interface BrowseResult {
  path: string
  parent: string | null
  entries: DirEntry[]
}

const emit = defineEmits<{ select: [path: string] }>()

const isOpen = ref(false)
const current = ref('')
const parent = ref<string | null>(null)
const entries = ref<DirEntry[]>([])
const loading = ref(false)
const fetchError = ref<string | null>(null)

async function browse(path: string): Promise<void> {
  loading.value = true
  fetchError.value = null
  try {
    const res = await fetch(
      `/api/system/browse?path=${encodeURIComponent(path)}`,
    )
    if (!res.ok) throw new Error(`HTTP ${res.status}`)
    const data = (await res.json()) as BrowseResult
    current.value = data.path
    parent.value = data.parent
    entries.value = data.entries
  } catch (e) {
    fetchError.value = e instanceof Error ? e.message : 'Browse failed'
  } finally {
    loading.value = false
  }
}

function openPicker(): void {
  isOpen.value = true
  void browse('~')
}

function close(): void {
  isOpen.value = false
}

function select(): void {
  emit('select', current.value)
  close()
}
</script>

<template>
  <div class="dir-picker">
    <button
      type="button"
      class="dir-picker__trigger"
      :aria-expanded="isOpen"
      aria-label="Browse directories"
      title="Browse directories"
      @click="openPicker"
    >
      📂
    </button>

    <div
      v-if="isOpen"
      class="dir-picker__overlay"
      @click.self="close"
    />

    <div
      v-if="isOpen"
      class="dir-picker__panel"
      role="dialog"
      aria-label="Directory picker"
    >
      <header class="dir-picker__head">
        <button
          v-if="parent != null"
          type="button"
          class="dir-picker__up"
          title="Go up"
          @click="browse(parent!)"
        >
          ↑ up
        </button>
        <code class="dir-picker__path">{{ current || '…' }}</code>
        <button
          type="button"
          class="dir-picker__close"
          aria-label="Close"
          @click="close"
        >
          ✕
        </button>
      </header>

      <p
        v-if="fetchError"
        class="dir-picker__error"
      >
        {{ fetchError }}
      </p>

      <p
        v-else-if="loading"
        class="dir-picker__loading"
      >
        Loading…
      </p>

      <ul
        v-else
        class="dir-picker__list"
      >
        <li
          v-if="entries.length === 0"
          class="dir-picker__empty"
        >
          No subdirectories
        </li>
        <li
          v-for="entry in entries"
          :key="entry.path"
        >
          <button
            type="button"
            class="dir-picker__entry"
            @click="browse(entry.path)"
          >
            📁 {{ entry.name }}
          </button>
        </li>
      </ul>

      <footer class="dir-picker__footer">
        <button
          type="button"
          class="dir-picker__select"
          :disabled="loading || current === ''"
          @click="select"
        >
          Select this folder
        </button>
      </footer>
    </div>
  </div>
</template>

<style scoped>
.dir-picker {
  position: relative;
  display: inline-flex;
}

.dir-picker__trigger {
  padding: 0.35em 0.55em;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  cursor: pointer;
  font-size: 1rem;
  line-height: 1;
}

.dir-picker__trigger:hover {
  border-color: var(--color-accent);
}

.dir-picker__overlay {
  position: fixed;
  inset: 0;
  z-index: 99;
}

.dir-picker__panel {
  position: absolute;
  top: calc(100% + 4px);
  left: 0;
  z-index: 100;
  width: 360px;
  max-height: 55vh;
  display: flex;
  flex-direction: column;
  border: 1px solid var(--color-border);
  border-radius: 8px;
  background: var(--color-surface);
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.25);
  overflow: hidden;
}

.dir-picker__head {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  padding: 0.5rem 0.6rem;
  border-bottom: 1px solid var(--color-border);
  background: var(--color-bg);
  flex-shrink: 0;
}

.dir-picker__path {
  flex: 1;
  font-size: 0.75em;
  font-family: var(--font-mono);
  color: var(--color-text-dim);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.dir-picker__up,
.dir-picker__close {
  padding: 0.2em 0.45em;
  border: 1px solid var(--color-border);
  border-radius: 4px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-size: 0.78em;
  cursor: pointer;
  flex-shrink: 0;
}

.dir-picker__up:hover,
.dir-picker__close:hover {
  border-color: var(--color-accent);
}

.dir-picker__list {
  list-style: none;
  margin: 0;
  padding: 0.3rem 0;
  overflow-y: auto;
  flex: 1;
}

.dir-picker__entry {
  display: block;
  width: 100%;
  text-align: left;
  padding: 0.3em 0.75em;
  background: none;
  border: none;
  font: inherit;
  font-size: 0.88em;
  font-family: var(--font-mono);
  color: var(--color-text);
  cursor: pointer;
}

.dir-picker__entry:hover {
  background: rgba(255, 255, 255, 0.05);
}

.dir-picker__empty,
.dir-picker__loading,
.dir-picker__error {
  padding: 0.5rem 0.75rem;
  margin: 0;
  font-size: 0.85em;
  color: var(--color-text-dim);
}

.dir-picker__error {
  color: #ff6b6b;
}

.dir-picker__footer {
  border-top: 1px solid var(--color-border);
  padding: 0.5rem 0.6rem;
  flex-shrink: 0;
  background: var(--color-bg);
}

.dir-picker__select {
  width: 100%;
  padding: 0.45em 0.9em;
  border-radius: 6px;
  border: 1px solid var(--color-accent, #4a90d9);
  background: var(--color-accent, #4a90d9);
  color: #fff;
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.dir-picker__select:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}

.dir-picker__select:not(:disabled):hover {
  filter: brightness(1.1);
}
</style>
