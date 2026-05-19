<script setup lang="ts">
// New Run wizard — step 2: run options (spec §9.1).
//
// `max_iters`, `iter_timeout`, and a model override. There is NO
// settings endpoint in the MVP, so defaults come from the server when
// these are omitted: every field is OPTIONAL and "leave blank = server
// default". The parent only forwards fields the user actually set.
//
// `model` override: spec §9.1 lists it for parity, but the generated
// `RunCreate` schema has NO model field (verified in schema.d.ts —
// RunCreate = {project_id, prompt_body?, prompt_id?, max_iters?,
// iter_timeout?}). So the input is rendered DISABLED with a "server
// default" note and is never sent to POST /api/runs. Do not invent an
// API field; when the backend grows one, re-enable here.

const props = defineProps<{
  maxIters: number | null
  iterTimeout: number | null
}>()

const emit = defineEmits<{
  'update:maxIters': [number | null]
  'update:iterTimeout': [number | null]
}>()

/** Parse a numeric input: blank → null (use server default). */
function parseNum(ev: Event): number | null {
  const raw = (ev.target as HTMLInputElement).value.trim()
  return raw === '' ? null : Number(raw)
}

function onMaxIters(ev: Event): void {
  emit('update:maxIters', parseNum(ev))
}

function onIterTimeout(ev: Event): void {
  emit('update:iterTimeout', parseNum(ev))
}
</script>

<template>
  <section class="step">
    <h2 class="step__title">
      2. Options
    </h2>
    <p class="step__hint">
      All optional — leave blank to use the server defaults.
    </p>

    <label class="field">
      <span class="field__label">Max iters</span>
      <input
        type="number"
        name="max-iters"
        min="1"
        :value="props.maxIters ?? ''"
        placeholder="server default"
        @input="onMaxIters"
      >
    </label>

    <label class="field">
      <span class="field__label">Iter timeout (seconds)</span>
      <input
        type="number"
        name="iter-timeout"
        min="1"
        :value="props.iterTimeout ?? ''"
        placeholder="server default"
        @input="onIterTimeout"
      >
    </label>

    <label class="field">
      <span class="field__label">Model override</span>
      <input
        type="text"
        name="model"
        disabled
        placeholder="server default (no API field in MVP)"
      >
      <span class="field__note">
        The run-create API has no model field in the MVP; the server
        default is always used.
      </span>
    </label>
  </section>
</template>

<style scoped>
.step__title {
  font-size: 1.1rem;
  margin: 0 0 0.5rem;
}

.step__hint {
  color: var(--color-text-dim);
  font-size: 0.88em;
  margin: 0 0 1rem;
}

.field {
  display: flex;
  flex-direction: column;
  gap: 0.25rem;
  margin-bottom: 1rem;
  max-width: 360px;
}

.field__label {
  font-size: 0.82em;
  color: var(--color-text-dim);
}

.field input {
  padding: 0.45em 0.6em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
}

.field input:disabled {
  opacity: 0.55;
}

.field__note {
  font-size: 0.78em;
  color: var(--color-text-dim);
}
</style>
