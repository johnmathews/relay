<script setup lang="ts">
// Project view (`/projects/:id`) — spec §9.1 "Project view": the
// per-project hub. A header (project name + root_path + "New run") and
// THREE tab-switched panes:
//
//   • Runs pane    — this project's runs (active + recent) via the W2
//     run-list query filtered by project_id. Click a run → /runs/:id.
//   • Prompts pane — saved prompts for the project. W8 filled the
//     extension point left by W5: the [list | detail] grid now hosts
//     PromptList (left) + a detail column that renders the selected
//     prompt (MarkdownRender) with Edit / Delete / Version-history
//     actions, swapping in PromptEditor (create/edit) or PromptVersions
//     (read-only history). The pane structure was NOT changed — the
//     same [list | detail] grid, the same `prompt-detail` testid.
//   • Files pane   — composes W6's FileTree + FileViewer scoped to this
//     project; this view hosts them and owns only the selected-path
//     wiring (W6's `files` store holds the ephemeral selection).
//
// Server reads go through the existing Pinia Colada hooks (no new
// queries were needed — W2's `useRunsQuery` already covers a
// project-scoped run list, W3's `usePromptsQuery` the prompt list, W6's
// queries the file browser). Ephemeral UI (active tab, selected prompt)
// is local component / W6-store state, never Colada (spec §9.2).

import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import AsyncBoundary from '@/components/shared/AsyncBoundary.vue'
import StatusBadge from '@/components/shared/StatusBadge.vue'
import ActionButton from '@/components/shared/ActionButton.vue'
import FileTree from '@/components/files/FileTree.vue'
import FileViewer from '@/components/files/FileViewer.vue'
import MarkdownRender from '@/components/files/MarkdownRender.vue'
import PromptList from '@/components/prompts/PromptList.vue'
import PromptEditor from '@/components/prompts/PromptEditor.vue'
import PromptVersions from '@/components/prompts/PromptVersions.vue'
import {
  useProjectQuery,
  useRunsQuery,
  usePromptsQuery,
  useDeletePromptMutation,
  useDeleteProjectMutation,
  asAsyncState,
  projectFileSource,
  type Prompt,
} from '@/lib/queries'
import { useBrowserUiStore } from '@/stores/files'

const props = defineProps<{ id: string }>()
const projectId = computed(() => Number(props.id))

const router = useRouter()

const projectQuery = useProjectQuery(projectId)
const project = computed(() => projectQuery.data.value ?? null)
const projectState = asAsyncState(projectQuery)

// ── Runs pane ────────────────────────────────────────────────────────
// W2's run-list query already covers a project-scoped list (the hub
// uses it with `{ projectId, limit: 1 }`); here we list the project's
// runs newest-first. No new query/key was needed.
const runsQuery = useRunsQuery(() => ({ projectId: projectId.value }))
const runs = computed(() => runsQuery.data.value ?? [])
const runsState = asAsyncState(runsQuery)

function openRun(runId: string): void {
  void router.push({ name: 'run-detail', params: { id: runId } })
}

// ── Prompts pane (W8 — full CRUD + read-only version history) ────────
const promptsQuery = usePromptsQuery(projectId)
const prompts = computed(() => promptsQuery.data.value ?? [])
const promptsState = asAsyncState(promptsQuery)

/** The selected prompt (its latest version row), or null. */
const selectedPrompt = ref<Prompt | null>(null)
/**
 * What the detail column shows: a read-only render of the selected
 * prompt ('view'), the create/edit form ('create' | 'edit'), or the
 * read-only version history ('versions'). Ephemeral UI state.
 */
type PromptMode = 'view' | 'create' | 'edit' | 'versions'
const promptMode = ref<PromptMode>('view')
/** Whether the delete-confirm step is showing for the selection. */
const confirmDeletePrompt = ref(false)

const deletePrompt = useDeletePromptMutation()

function selectPrompt(p: Prompt): void {
  // Clicking a row selects it and returns to the read-only view.
  selectedPrompt.value = p
  promptMode.value = 'view'
  confirmDeletePrompt.value = false
}
function startNewPrompt(): void {
  selectedPrompt.value = null
  promptMode.value = 'create'
  confirmDeletePrompt.value = false
}
function startEditPrompt(): void {
  if (selectedPrompt.value) promptMode.value = 'edit'
}
function showVersions(): void {
  if (selectedPrompt.value) promptMode.value = 'versions'
}
function cancelPromptForm(): void {
  promptMode.value = 'view'
}
/** A create/edit succeeded → select the new version row, back to view. */
function onPromptSaved(p: Prompt): void {
  selectedPrompt.value = p
  promptMode.value = 'view'
}
async function onConfirmDeletePrompt(): Promise<void> {
  const target = selectedPrompt.value
  if (!target) return
  try {
    await deletePrompt.mutateAsync(target.id)
    // onSuccess already invalidated keys.prompts(); clear selection.
    selectedPrompt.value = null
    promptMode.value = 'view'
    confirmDeletePrompt.value = false
  } catch {
    // Surfaced inline via deletePrompt.error.
  }
}

// ── Files pane ───────────────────────────────────────────────────────
// W6's FileTree + FileViewer are now source-agnostic (W7 abstraction):
// we hand them the project file-browser source. Its per-source
// ephemeral UI store holds the selection (spec §9.2).
const fileSource = computed(() => projectFileSource(projectId.value))
const filesStore = computed(() =>
  useBrowserUiStore(fileSource.value.storeId),
)
const selectedFilePath = computed(() => filesStore.value.selectedPath)
function onFileSelect(path: string): void {
  filesStore.value.selectFile(path)
}

// ── Tabs ─────────────────────────────────────────────────────────────
type Tab = 'runs' | 'prompts' | 'files'
const TABS: { id: Tab; label: string }[] = [
  { id: 'runs', label: 'Runs' },
  { id: 'prompts', label: 'Prompts' },
  { id: 'files', label: 'Files' },
]
const activeTab = ref<Tab>('runs')
function selectTab(t: Tab): void {
  activeTab.value = t
}
/** Roving arrow-key navigation across the tablist (accessibility). */
function onTabKey(e: KeyboardEvent, idx: number): void {
  if (e.key !== 'ArrowRight' && e.key !== 'ArrowLeft') return
  e.preventDefault()
  const delta = e.key === 'ArrowRight' ? 1 : -1
  const next = (idx + delta + TABS.length) % TABS.length
  activeTab.value = TABS[next]!.id
}

function goNewRun(): void {
  void router.push({ name: 'new-run', params: { id: props.id } })
}

// ── Unregister project (spec §9.1 — DELETE /api/projects/{id}) ────────
// Unregister only: it never deletes files on disk. A confirm step
// states that explicitly before the destructive call; on success we
// invalidate the project list and navigate back to the Hub.
const deleteProject = useDeleteProjectMutation()
const confirmUnregister = ref(false)

const unregisterError = computed<string | null>(() => {
  const e: unknown = deleteProject.error.value
  if (e == null) return null
  if (e instanceof Error) return e.message
  return 'Failed to unregister project.'
})

async function onConfirmUnregister(): Promise<void> {
  try {
    await deleteProject.mutateAsync(projectId.value)
    // onSuccess already invalidated keys.projects().
    confirmUnregister.value = false
    void router.push('/')
  } catch {
    // Surfaced inline via unregisterError.
  }
}
</script>

<template>
  <section class="project-view">
    <AsyncBoundary
      :loading="projectState.isLoading.value"
      :error="projectState.error.value"
    >
      <template v-if="project">
        <header class="project-view__header">
          <div class="project-view__title-row">
            <h1 class="project-view__title">
              {{ project.name }}
            </h1>
            <button
              type="button"
              class="project-view__new-run"
              data-testid="new-run-button"
              @click="goNewRun"
            >
              New run
            </button>
            <button
              type="button"
              class="project-view__unregister"
              data-testid="unregister-button"
              @click="confirmUnregister = true"
            >
              Unregister
            </button>
          </div>
          <p class="project-view__path">
            {{ project.root_path }}
          </p>
          <div
            v-if="confirmUnregister"
            class="project-view__confirm"
            role="alertdialog"
            aria-label="Confirm unregister"
            data-testid="unregister-confirm"
          >
            <p class="project-view__confirm-text">
              Unregister “{{ project.name }}” from relay? This only
              removes it from relay — it does NOT delete any files on
              disk. You can register the same path again later.
            </p>
            <p
              v-if="unregisterError"
              class="project-view__error"
              role="alert"
            >
              {{ unregisterError }}
            </p>
            <div class="project-view__confirm-actions">
              <ActionButton
                :loading="deleteProject.isLoading.value"
                data-testid="unregister-confirm-button"
                @click="onConfirmUnregister"
              >
                Unregister
              </ActionButton>
              <button
                type="button"
                class="project-view__confirm-cancel"
                data-testid="unregister-cancel-button"
                @click="confirmUnregister = false"
              >
                Cancel
              </button>
            </div>
          </div>
        </header>

        <div
          class="project-view__tabs"
          role="tablist"
          aria-label="Project sections"
        >
          <button
            v-for="(tab, idx) in TABS"
            :id="`project-tab-${tab.id}`"
            :key="tab.id"
            type="button"
            role="tab"
            class="project-view__tab"
            :class="{ 'project-view__tab--active': activeTab === tab.id }"
            :aria-selected="activeTab === tab.id"
            :tabindex="activeTab === tab.id ? 0 : -1"
            :aria-controls="`project-panel-${tab.id}`"
            :data-testid="`tab-${tab.id}`"
            @click="selectTab(tab.id)"
            @keydown="onTabKey($event, idx)"
          >
            {{ tab.label }}
          </button>
        </div>

        <!-- Runs pane -->
        <div
          v-show="activeTab === 'runs'"
          id="project-panel-runs"
          role="tabpanel"
          aria-labelledby="project-tab-runs"
          data-testid="panel-runs"
        >
          <AsyncBoundary
            :loading="runsState.isLoading.value"
            :error="runsState.error.value"
          >
            <p
              v-if="runs.length === 0"
              class="project-view__empty"
            >
              No runs for this project yet.
            </p>
            <ul
              v-else
              class="project-view__runs"
            >
              <li
                v-for="run in runs"
                :key="run.id"
              >
                <button
                  type="button"
                  class="project-view__run"
                  :data-testid="`run-row-${run.id}`"
                  @click="openRun(run.id)"
                >
                  <StatusBadge :status="run.status" />
                  <span class="project-view__run-prompt">
                    {{
                      run.prompt_id != null
                        ? `prompt #${run.prompt_id}`
                        : 'inline'
                    }}
                  </span>
                  <span class="project-view__run-meta">
                    {{ run.started_at }}
                  </span>
                  <span class="project-view__run-id">{{ run.id }}</span>
                </button>
              </li>
            </ul>
          </AsyncBoundary>
        </div>

        <!-- Prompts pane (VIEW-ONLY; W8 adds CRUD) -->
        <div
          v-show="activeTab === 'prompts'"
          id="project-panel-prompts"
          role="tabpanel"
          aria-labelledby="project-tab-prompts"
          data-testid="panel-prompts"
        >
          <!-- W8 filled this extension point: the SAME [list | detail]
               grid W5 left. PromptList replaces the inline list; the
               detail column hosts the read-only render + Edit / Delete /
               History actions, swapping in PromptEditor / PromptVersions.
               Pane structure unchanged (same grid + `prompt-detail`). -->
          <AsyncBoundary
            :loading="promptsState.isLoading.value"
            :error="promptsState.error.value"
          >
            <div class="project-view__prompts">
              <PromptList
                :prompts="prompts"
                :selected-id="selectedPrompt?.id ?? null"
                @select="selectPrompt"
                @new="startNewPrompt"
              />
              <div
                class="project-view__prompt-detail"
                data-testid="prompt-detail"
              >
                <PromptEditor
                  v-if="promptMode === 'create' || promptMode === 'edit'"
                  :mode="promptMode"
                  :project-id="projectId"
                  :prompt="selectedPrompt"
                  @saved="onPromptSaved"
                  @cancel="cancelPromptForm"
                />
                <PromptVersions
                  v-else-if="
                    promptMode === 'versions' && selectedPrompt
                  "
                  :prompt-id="selectedPrompt.id"
                  @close="cancelPromptForm"
                />
                <template v-else>
                  <p
                    v-if="!selectedPrompt"
                    class="project-view__empty"
                  >
                    Select a prompt to view it, or create a new one.
                  </p>
                  <template v-else>
                    <div class="project-view__prompt-actions">
                      <button
                        type="button"
                        class="project-view__prompt-action"
                        data-testid="prompt-edit"
                        @click="startEditPrompt"
                      >
                        Edit
                      </button>
                      <button
                        type="button"
                        class="project-view__prompt-action"
                        data-testid="prompt-history"
                        @click="showVersions"
                      >
                        Version history
                      </button>
                      <button
                        type="button"
                        class="project-view__prompt-action project-view__prompt-action--danger"
                        data-testid="prompt-delete"
                        @click="confirmDeletePrompt = true"
                      >
                        Delete
                      </button>
                    </div>
                    <div
                      v-if="confirmDeletePrompt"
                      class="project-view__confirm"
                      role="alertdialog"
                      aria-label="Confirm delete prompt"
                      data-testid="prompt-delete-confirm"
                    >
                      <p class="project-view__confirm-text">
                        Delete the prompt “{{ selectedPrompt.name }}”
                        and ALL of its versions? This removes the entire
                        version history and cannot be undone.
                      </p>
                      <div class="project-view__confirm-actions">
                        <ActionButton
                          :loading="deletePrompt.isLoading.value"
                          data-testid="prompt-delete-confirm-button"
                          @click="onConfirmDeletePrompt"
                        >
                          Delete prompt
                        </ActionButton>
                        <button
                          type="button"
                          class="project-view__confirm-cancel"
                          data-testid="prompt-delete-cancel-button"
                          @click="confirmDeletePrompt = false"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                    <MarkdownRender :source="selectedPrompt.body" />
                  </template>
                </template>
              </div>
            </div>
          </AsyncBoundary>
        </div>

        <!-- Files pane — composes W6's FileTree + FileViewer -->
        <div
          v-show="activeTab === 'files'"
          id="project-panel-files"
          role="tabpanel"
          aria-labelledby="project-tab-files"
          data-testid="panel-files"
          class="project-view__files"
        >
          <FileTree
            :source="fileSource"
            aria-label="Project files"
            @select="onFileSelect"
          />
          <FileViewer
            :source="fileSource"
            :path="selectedFilePath"
          />
        </div>
      </template>
    </AsyncBoundary>
  </section>
</template>

<style scoped>
.project-view {
  display: flex;
  flex-direction: column;
  gap: 1rem;
  max-width: 1100px;
}

.project-view__title-row {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.project-view__title {
  margin: 0;
  flex: 1;
  font-size: 1.4rem;
}

.project-view__new-run {
  padding: 0.45em 0.9em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.project-view__new-run:hover {
  border-color: var(--color-accent);
}

.project-view__unregister {
  padding: 0.45em 0.9em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-surface);
  color: var(--color-text-dim);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
}

.project-view__unregister:hover {
  border-color: #ff6b6b;
  color: #ff6b6b;
}

.project-view__confirm {
  margin-top: 0.75rem;
  border: 1px solid #ff6b6b;
  border-radius: 8px;
  padding: 0.85rem 1rem;
  background: var(--color-surface);
}

.project-view__confirm-text {
  margin: 0 0 0.75rem;
  font-size: 0.9em;
}

.project-view__confirm-actions {
  display: flex;
  align-items: center;
  gap: 1rem;
}

.project-view__confirm-cancel {
  background: none;
  border: none;
  color: var(--color-text-dim);
  font: inherit;
  cursor: pointer;
}

.project-view__confirm-cancel:hover {
  color: var(--color-text);
  text-decoration: underline;
}

.project-view__error {
  margin: 0 0 0.5rem;
  color: #ff6b6b;
  font-size: 0.85em;
}

.project-view__prompt-actions {
  display: flex;
  gap: 0.5rem;
  margin-bottom: 0.85rem;
}

.project-view__prompt-action {
  padding: 0.35em 0.7em;
  border-radius: 6px;
  border: 1px solid var(--color-border);
  background: var(--color-bg);
  color: var(--color-text);
  font: inherit;
  font-size: 0.85em;
  cursor: pointer;
}

.project-view__prompt-action:hover {
  border-color: var(--color-accent);
}

.project-view__prompt-action--danger:hover {
  border-color: #ff6b6b;
  color: #ff6b6b;
}

.project-view__path {
  margin: 0.35rem 0 0;
  font-family: var(--font-mono);
  font-size: 0.85em;
  color: var(--color-text-dim);
  word-break: break-all;
}

.project-view__tabs {
  display: flex;
  gap: 0.25rem;
  border-bottom: 1px solid var(--color-border);
}

.project-view__tab {
  padding: 0.5em 1em;
  border: 1px solid transparent;
  border-bottom: none;
  background: none;
  color: var(--color-text-dim);
  font: inherit;
  font-weight: 600;
  cursor: pointer;
  border-radius: 6px 6px 0 0;
}

.project-view__tab--active {
  color: var(--color-text);
  border-color: var(--color-border);
  background: var(--color-surface);
}

.project-view__empty {
  color: var(--color-text-dim);
  padding: 1rem 0;
}

.project-view__runs {
  list-style: none;
  margin: 0;
  padding: 0;
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.project-view__run {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  width: 100%;
  text-align: left;
  padding: 0.55rem 0.7rem;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  background: var(--color-surface);
  color: var(--color-text);
  font: inherit;
  cursor: pointer;
}

.project-view__run:hover {
  border-color: var(--color-accent);
}

.project-view__run-prompt {
  font-weight: 600;
}

.project-view__run-meta,
.project-view__run-id {
  font-size: 0.78em;
  color: var(--color-text-dim);
  font-family: var(--font-mono);
}

.project-view__run-id {
  margin-left: auto;
}

.project-view__prompts {
  display: grid;
  grid-template-columns: minmax(180px, 260px) 1fr;
  gap: 1rem;
  align-items: start;
}

.project-view__prompt-detail {
  min-width: 0;
  border: 1px solid var(--color-border);
  border-radius: 6px;
  padding: 0.85rem 1rem;
  background: var(--color-surface);
}

.project-view__files {
  display: grid;
  grid-template-columns: minmax(200px, 320px) 1fr;
  gap: 1rem;
  align-items: start;
}
</style>
