// Pinia Colada query layer — the single place the dashboard talks to the
// REST API for cached, revalidated reads/writes.
//
// Spec §9.2: `Pinia Colada` is the REST cache + automatic revalidation
// layer; plain Pinia stores hold only ephemeral UI state. Views call the
// `useXxxQuery` / `useXxxMutation` hooks below — they do NOT call the
// `api` client directly, and they do NOT cache server data in a Pinia
// store (that would duplicate Colada's cache).
//
// ── Query-key scheme (W3/W4/W5 MUST follow this) ────────────────────────
// Keys are arrays. The first element is the resource collection; deeper
// elements narrow it. Invalidating a prefix invalidates everything under
// it (Colada matches by key prefix unless `exact: true`).
//
//   ['projects']                       → list of all projects
//   ['projects', id]                   → one project by id
//   ['runs']                           → all run-list queries (prefix)
//   ['runs', { projectId, status,      → a filtered run list
//              limit, offset }]
//   ['runs', 'detail', runId]          → one run's detail (W3/W4/W5)
//   ['runs', 'events', runId,          → a paginated page of a run's
//            { afterSeq, limit,           persisted events (W4 replay).
//              offset }]                  Nested under `['runs', …]` so a
//                                         broad `invalidate(['runs'])`
//                                         (post-mutation / SSE push) also
//                                         refreshes a replayed event list.
//   ['prompts']                        → all prompt queries (prefix)
//   ['prompts', { projectId }]         → saved prompts for a project
//   ['prompts', 'detail', promptId]    → one prompt (a version row)
//   ['prompts', 'versions', promptId]  → all versions of a prompt (W8
//                                         read-only history). Nested
//                                         under `['prompts', …]` so a
//                                         broad prompt invalidation
//                                         (create/edit/delete) also
//                                         refreshes an open history view.
//   ['prompts', 'preview', { ... }]    → a side-effect-free run preview
//   ['files', projectId]               → all file-browser queries for a
//                                         project (prefix)
//   ['files', projectId, 'tree', path] → one directory listing
//                                         (lazy-expanded by FileTree)
//   ['files', projectId, 'content',    → one file's raw content
//             path]                       (FileViewer / DiffRender)
//   ['artifacts', runId]               → all artifacts-browser queries
//                                         for a run (prefix)
//   ['artifacts', runId, 'tree', path] → one artifacts dir listing
//                                         (lazy-expanded by FileTree)
//   ['artifacts', runId, 'content',    → one artifact's raw content
//             path]                       (FileViewer)
//
// `artifacts` (W7): the read-only sandboxed RUN-artifacts browser
// (ADR-25; sandbox root = `data_dir/runs/<run_id>`). It is the EXACT
// analogue of `files` but scoped to a run instead of a project, and it
// reuses the same `FileEntry`/`FileListing`/`FileContent` shapes and the
// same FileTree/FileViewer render pipeline via the `BrowserSource`
// abstraction (see `projectFileSource` / `runArtifactSource` below). The
// backend is single-sourced (ADR-25 — artifacts routes are thin adapters
// over the project file-browser handlers); the frontend stays
// single-sourced the same way: ONE tree + ONE viewer, the source object
// just swaps which endpoint family + UI-state instance is used.
//
// `files` (W6): the read-only sandboxed file browser. Listings and file
// content are keyed per (projectId, path) so each expanded directory
// and each opened file is its own cache entry; both nest under
// `['files', projectId]` so a project-scoped invalidation drops the
// whole subtree. Server data lives ONLY in the Colada cache — the
// `stores/files.ts` Pinia store holds just ephemeral UI state (expanded
// dirs, selection) per spec §9.2.
//
// `prompts` (W3 read / W8 write): the saved-prompt library. W8 adds the
// write mutations (create / edit / delete) and the read-only version
// history (`['prompts', 'versions', id]`). Prompts are *versioned*: an
// edit is a snapshot bump (`PUT /api/prompts/{id}` inserts a NEW row at
// `max(version)+1`; old rows are never mutated — history is preserved);
// a delete removes ALL versions of that `(project_id, name)`. Every
// write invalidates the broad `['prompts']` prefix so the project's
// prompt list AND any open version-history view both refresh.
//
// `prompts` (W3): the saved-prompt library + the side-effect-free run
// *preview*. Preview is keyed under `['prompts', 'preview', …]` rather
// than `['runs', …]` because by contract it creates no run row/event/dir
// (`docs/api.md` — zero side effects); it is a prospective render of a
// prompt+preamble, so it belongs to the prompt resource, not runs. The
// preview key includes the full selection (project + prompt source +
// phase) so changing the prompt or options yields a fresh cache entry.
//
// Rationale: a flat, serializable, prefix-nestable scheme. After a
// mutation (register/delete project, create run) or an SSE push (W4),
// call `invalidate(key)` with the broadest affected prefix — e.g.
// `invalidate(['projects'])` refetches the project list AND any
// per-project run-status queries are refreshed via `invalidate(['runs'])`.
// Creating a run invalidates `['runs']` (the new run must appear in
// every run list / hub status).
//
// Keep this file the single source of query keys: never hand-write a key
// array in a component — use the `keys` factory so the scheme stays
// consistent and refactor-safe.

import { useQuery, useMutation, useQueryCache } from '@pinia/colada'
import type { UseQueryReturn, UseMutationReturn } from '@pinia/colada'
import { computed, type MaybeRefOrGetter, toValue } from 'vue'
import { api } from '@/api/client'
import type { components } from '@/api/schema'

/** A registered project (`GET /api/projects` element). */
export type Project = components['schemas']['ProjectOut']
/** Request body for registering a project (`POST /api/projects`). */
export type ProjectCreate = components['schemas']['ProjectCreate']
/** A run summary (`GET /api/runs` element). */
export type Run = components['schemas']['RunOut']
/** A saved prompt (`GET /api/prompts` element / `GET /api/prompts/{id}`). */
export type Prompt = components['schemas']['PromptOut']
/** Request body for creating a prompt (`POST /api/prompts`). */
export type PromptCreate = components['schemas']['PromptCreate']
/** Request body for editing a prompt (`PUT /api/prompts/{id}` — body only). */
export type PromptUpdate = components['schemas']['PromptUpdate']
/** A run's full detail incl. `iters[]` (`GET /api/runs/{id}`). */
export type RunDetail = components['schemas']['RunDetailOut']
/** One iter row inside a `RunDetail` (`iters[]` element). */
export type Iter = components['schemas']['IterOut']
/** A persisted event row (`GET /api/runs/{id}/events` element). */
export type EventRow = components['schemas']['EventOut']
/** A page of persisted events (`GET /api/runs/{id}/events`). */
export type PaginatedEvents = components['schemas']['PaginatedEventsOut']
/** Request body for creating a run (`POST /api/runs`). */
export type RunCreate = components['schemas']['RunCreate']
/** The side-effect-free run preview (`GET /api/runs/{projectId}/preview`). */
export type Preview = components['schemas']['PreviewOut']

export type SettingsDefaults = components['schemas']['SettingsDefaultsOut']

// The file-browser endpoints return bare dicts on the backend, so the
// generated schema types them as `unknown` (no Pydantic model). The
// shapes are fixed by `docs/api.md` (File browser section), so we
// declare them explicitly here rather than guess at call sites.

/** One entry in a directory listing (`GET .../files`). */
export interface FileEntry {
  /** Base name (no path). */
  name: string
  /** Directory vs. regular file. */
  is_dir: boolean
  /** Size in bytes (0 for directories). */
  size: number
  /** Last-modified epoch seconds. */
  modified: number
}

/**
 * A directory listing. `entries` is already dirs-first then name-asc
 * (backend contract — the UI preserves the order, does not re-sort).
 */
export interface FileListing {
  /** The listed directory path, relative to the project root. */
  path: string
  entries: FileEntry[]
}

/** A file's raw content (`GET .../files/{file_path}`). */
export interface FileContent {
  /** The file path, relative to the project root. */
  path: string
  /** Decoded text content (binary files never reach here — 415). */
  content: string
  /** Size in bytes. */
  size: number
  /** Last-modified epoch seconds. */
  modified: number
}

/**
 * The prompt source for a preview/create: exactly one of an existing
 * saved-prompt id OR an inline body (mirrors the API's one-of contract).
 */
export type PromptSource =
  | { promptId: number }
  | { promptBody: string }

/** Selection passed to the run preview (project + prompt source + phase). */
export interface PreviewSelection {
  /** The project id (the preview path segment — NOT a run id). */
  projectId: number
  /** Exactly one of an existing prompt id or an inline body. */
  source: PromptSource
  /** Optional phase override (`docs/api.md` preview `phase` query param). */
  phase?: string
}

/** Filters accepted by `GET /api/runs`. */
export interface RunListFilters {
  projectId?: number
  status?: string
  /**
   * Run mode filter (W1). `"task"` for the engteam-style chained-iter
   * runs, `"chat"` for the conversational shell. Omit to include both.
   * The Chats list in ProjectView uses `'chat'` to get the
   * project-scoped chats; the existing Runs list omits it (task and
   * chat runs would otherwise mix in one list, which is the wrong UX
   * per docs/proposals/chat-mode.md decision 9).
   */
  mode?: string
  limit?: number
  offset?: number
  /**
   * When true, include child runs (parent_run_id NOT NULL). Default
   * false — the run lists default-hide children so the list stays
   * readable when fanout is in use (spec.md §9.1, 9e).
   */
  includeChildren?: boolean
}

/** A page window for the paginated event-replay endpoint. */
export interface EventPage {
  /** Only events with `seq > afterSeq` (0 = from the start). */
  afterSeq?: number
  limit?: number
  offset?: number
}

/**
 * Query-key factory. The single source of truth for cache keys; always
 * build keys through this so the scheme stays consistent (see top of
 * file for the documented scheme).
 */
export const keys = {
  /** All project queries (prefix). */
  projects: (): readonly ['projects'] => ['projects'] as const,
  /** One project by id. */
  project: (id: number): readonly ['projects', number] =>
    ['projects', id] as const,
  /** All run-list queries (prefix). */
  runs: (): readonly ['runs'] => ['runs'] as const,
  /** A filtered run list. */
  runList: (
    filters: RunListFilters,
  ): readonly ['runs', RunListFilters] => ['runs', filters] as const,
  /** One run's detail (used by W3/W4/W5). */
  runDetail: (runId: string): readonly ['runs', 'detail', string] =>
    ['runs', 'detail', runId] as const,
  /**
   * Direct children of a parent run (`GET /api/runs/{id}/children`).
   * Nested under `['runs', …]` so `invalidate(keys.runs())` (post-
   * mutation / SSE push) also refreshes an open Children pane.
   */
  runChildren: (runId: string): readonly ['runs', 'children', string] =>
    ['runs', 'children', runId] as const,
  /**
   * A paginated page of a run's persisted events (W4 replay path for a
   * finished run). Keyed by the page window so each page is its own
   * cache entry; nested under `['runs', …]` so `invalidate(keys.runs())`
   * also drops it.
   */
  runEvents: (
    runId: string,
    page: EventPage,
  ): readonly ['runs', 'events', string, EventPage] =>
    ['runs', 'events', runId, page] as const,
  /** All prompt queries (prefix). */
  prompts: (): readonly ['prompts'] => ['prompts'] as const,
  /** The saved prompts for a project (`GET /api/prompts?project_id=`). */
  promptList: (
    projectId: number,
  ): readonly ['prompts', { projectId: number }] =>
    ['prompts', { projectId }] as const,
  /** One prompt by id (a specific version row). */
  promptDetail: (
    promptId: number,
  ): readonly ['prompts', 'detail', number] =>
    ['prompts', 'detail', promptId] as const,
  /**
   * All versions of a prompt, asc (`GET /api/prompts/{id}/versions`;
   * W8 read-only history). Nested under `['prompts', …]` so any prompt
   * write (`invalidate(keys.prompts())`) also drops it.
   */
  promptVersions: (
    promptId: number,
  ): readonly ['prompts', 'versions', number] =>
    ['prompts', 'versions', promptId] as const,
  /**
   * A side-effect-free run preview. Keyed by the full selection so a
   * different prompt source / phase is a distinct cache entry — this is
   * what lets W3 detect "selection changed → re-preview required".
   */
  preview: (
    sel: PreviewSelection,
  ): readonly ['prompts', 'preview', PreviewSelection] =>
    ['prompts', 'preview', sel] as const,
  /** All file-browser queries for a project (prefix). */
  files: (projectId: number): readonly ['files', number] =>
    ['files', projectId] as const,
  /**
   * One directory listing within a project. `path` is '' for the
   * project root. Nested under `keys.files(projectId)`.
   */
  fileTree: (
    projectId: number,
    path: string,
  ): readonly ['files', number, 'tree', string] =>
    ['files', projectId, 'tree', path] as const,
  /**
   * One file's raw content within a project. Nested under
   * `keys.files(projectId)`.
   */
  fileContent: (
    projectId: number,
    path: string,
  ): readonly ['files', number, 'content', string] =>
    ['files', projectId, 'content', path] as const,
  /** All artifacts-browser queries for a run (prefix) — ADR-25/W7. */
  artifacts: (runId: string): readonly ['artifacts', string] =>
    ['artifacts', runId] as const,
  /**
   * One artifacts-dir listing within a run. `path` is '' for the run's
   * artifacts root. Nested under `keys.artifacts(runId)` so a
   * run-scoped invalidation drops the whole subtree.
   */
  artifactTree: (
    runId: string,
    path: string,
  ): readonly ['artifacts', string, 'tree', string] =>
    ['artifacts', runId, 'tree', path] as const,
  /**
   * One artifact's raw content within a run. Nested under
   * `keys.artifacts(runId)`.
   */
  artifactContent: (
    runId: string,
    path: string,
  ): readonly ['artifacts', string, 'content', string] =>
    ['artifacts', runId, 'content', path] as const,
  /** Server-side defaults the New Run wizard prefills. */
  settingsDefaults: (): readonly ['settings', 'defaults'] =>
    ['settings', 'defaults'] as const,
} as const

/**
 * Normalize an openapi-fetch `{ data, error }` result into the resolved
 * value or a thrown `Error` (Colada represents failures as rejections).
 */
function unwrap<T>(
  result: { data?: T; error?: unknown; response: Response },
): T {
  if (result.error !== undefined) {
    throw new ApiError(result.response.status, result.error)
  }
  return result.data as T
}

/**
 * An API error carrying the HTTP status and the parsed error body so
 * forms can surface a useful inline message (e.g. a 4xx validation
 * failure). `message` is a best-effort human string.
 */
export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, body: unknown) {
    super(ApiError.describe(status, body))
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }

  private static describe(status: number, body: unknown): string {
    if (
      body !== null &&
      typeof body === 'object' &&
      'detail' in body &&
      typeof (body as { detail: unknown }).detail === 'string'
    ) {
      return (body as { detail: string }).detail
    }
    return `Request failed with status ${status}`
  }
}

/**
 * `useQuery` for the server-side run-option defaults
 * (`GET /api/system/defaults`). Used by the New Run wizard to prefill
 * `max_iters` / `iter_timeout` with concrete numbers — clearer than the
 * opaque "server default" placeholder the form used to show.
 */
export function useSettingsDefaultsQuery(): UseQueryReturn<SettingsDefaults> {
  return useQuery({
    key: keys.settingsDefaults(),
    query: async () => unwrap(await api.GET('/api/system/defaults')),
  })
}

/** `useQuery` for the full project list (`GET /api/projects`). */
export function useProjectsQuery(): UseQueryReturn<Project[]> {
  return useQuery({
    key: keys.projects(),
    query: async () => unwrap(await api.GET('/api/projects')),
  })
}

/** `useQuery` for a single project (`GET /api/projects/{id}`). */
export function useProjectQuery(
  id: MaybeRefOrGetter<number>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<Project> {
  return useQuery({
    key: () => keys.project(toValue(id)),
    enabled: () => toValue(enabled) && toValue(id) > 0,
    query: async () =>
      unwrap(
        await api.GET('/api/projects/{project_id}', {
          params: { path: { project_id: toValue(id) } },
        }),
      ),
  })
}

/**
 * `useQuery` for a filtered run list (`GET /api/runs`). Used by the hub
 * with `{ projectId, limit: 1 }` to get a project's latest run status —
 * an accepted N+1 at single-user scale (Phase-4 scope note).
 */
export function useRunsQuery(
  filters: MaybeRefOrGetter<RunListFilters>,
): UseQueryReturn<Run[]> {
  return useQuery({
    key: () => keys.runList(toValue(filters)),
    query: async () => {
      const f = toValue(filters)
      return unwrap(
        await api.GET('/api/runs', {
          params: {
            query: {
              project_id: f.projectId,
              status: f.status,
              mode: f.mode,
              limit: f.limit,
              offset: f.offset,
              include_children: f.includeChildren,
            },
          },
        }),
      )
    },
  })
}

/**
 * `useQuery` for the project's chat-mode runs
 * (`GET /api/runs?project_id=…&mode=chat`). Thin specialisation of
 * {@link useRunsQuery} that pre-binds the mode filter and reuses the
 * same `keys.runList(...)` cache key under the broad `['runs']`
 * prefix, so any cache invalidation that drops `keys.runs()` (a new
 * run created, a close mutation, an SSE lifecycle event from the
 * events store) refreshes the chats list in lockstep with task lists.
 *
 * W5 — docs/proposals/chat-mode.md. Decision 9: chats are runs but
 * are visually segregated, so the dashboard wants two separate
 * queries even though they share an endpoint. The mode filter on
 * `/api/runs` lets us do that without a dedicated endpoint.
 *
 * The query intentionally keeps the response sort the backend
 * provides (created_at descending, established by the existing
 * `list_runs` order) — re-sorting client-side would diverge from the
 * Runs pane's order on the same view, which is the wrong UX.
 */
export function useProjectChatsQuery(
  projectId: MaybeRefOrGetter<number>,
): UseQueryReturn<Run[]> {
  return useRunsQuery(() => ({
    projectId: toValue(projectId),
    mode: 'chat',
  }))
}

/**
 * `useMutation` to create a new chat-mode run
 * (`POST /api/runs` body `{project_id, mode: "chat"}`; W1 +
 * docs/proposals/chat-mode.md). Returns the created Run so callers
 * can navigate to `/chats/<id>` on success. Invalidates `keys.runs()`
 * so the project view's Chats list (and any hub status) refreshes.
 */
export function useCreateChatMutation(): UseMutationReturn<
  Run,
  number,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (projectId: number) =>
      unwrap(
        await api.POST('/api/runs', {
          body: { project_id: projectId, mode: 'chat' },
        }),
      ),
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.runs() })
    },
  })
}

/**
 * `useMutation` to register a project (`POST /api/projects`). Idempotent
 * on `root_path` server-side. On success the caller should invalidate
 * `keys.projects()` so the hub list refreshes.
 */
export function useRegisterProjectMutation(): UseMutationReturn<
  Project,
  ProjectCreate,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (body: ProjectCreate) =>
      unwrap(await api.POST('/api/projects', { body })),
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.projects() })
    },
  })
}

/**
 * `useMutation` to unregister a project
 * (`DELETE /api/projects/{id}`). Invalidates the project list on success.
 */
export function useDeleteProjectMutation(): UseMutationReturn<
  void,
  number,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (id: number) => {
      unwrap(
        await api.DELETE('/api/projects/{project_id}', {
          params: { path: { project_id: id } },
        }),
      )
    },
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.projects() })
    },
  })
}

/**
 * `useQuery` for a project's saved prompts (`GET /api/prompts?project_id=`).
 * Returns the latest version of each prompt (server contract). Used by
 * the New Run wizard's step-1 prompt picker.
 */
export function usePromptsQuery(
  projectId: MaybeRefOrGetter<number>,
): UseQueryReturn<Prompt[]> {
  return useQuery({
    key: () => keys.promptList(toValue(projectId)),
    query: async () =>
      unwrap(
        await api.GET('/api/prompts', {
          params: { query: { project_id: toValue(projectId) } },
        }),
      ),
  })
}

/**
 * `useQuery` for the full version history of a prompt
 * (`GET /api/prompts/{id}/versions`) — all versions ascending. W8's
 * read-only history view. The endpoint returns `{versions: PromptOut[]}`;
 * this hook unwraps to the `versions` array directly. `enabled` gates
 * the fetch so history is only loaded when the panel is opened.
 */
export function usePromptVersionsQuery(
  promptId: MaybeRefOrGetter<number | null>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<Prompt[]> {
  return useQuery({
    // Non-null assertion is safe: `enabled` gates the fetch off when
    // the id is null, so the key/query only run for a real prompt id.
    key: () => keys.promptVersions(toValue(promptId) ?? 0),
    enabled: () => toValue(enabled) && toValue(promptId) != null,
    query: async () =>
      unwrap(
        await api.GET('/api/prompts/{prompt_id}/versions', {
          params: { path: { prompt_id: toValue(promptId) as number } },
        }),
      ).versions,
  })
}

/**
 * `useMutation` to create a prompt (`POST /api/prompts`) → version 1.
 * Body is `{project_id?, name, body}`. A duplicate `(project_id, name)`
 * → 409 and an unknown project → 404; both surface as an `ApiError`
 * carrying `status` so the editor can show an inline message. On success
 * we invalidate the broad `keys.prompts()` prefix so the project's
 * prompt list (and any open history) refreshes.
 */
export function useCreatePromptMutation(): UseMutationReturn<
  Prompt,
  PromptCreate,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (body: PromptCreate) =>
      unwrap(await api.POST('/api/prompts', { body })),
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.prompts() })
    },
  })
}

/** Arguments for the edit mutation: which prompt id + the new body. */
export interface UpdatePromptArgs {
  /** The prompt-version id being edited (the PUT path segment). */
  id: number
  /** The new body — a snapshot bump (NEW version row; history kept). */
  body: string
}

/**
 * `useMutation` to edit a prompt (`PUT /api/prompts/{id}`). This is a
 * SNAPSHOT BUMP per `docs/api.md`: the server inserts a NEW row at
 * `max(version)+1` and leaves every old version untouched — editing
 * never mutates history. On success we invalidate the broad
 * `keys.prompts()` prefix so the list shows the new latest version AND
 * an open version-history view picks up the new row.
 */
export function useUpdatePromptMutation(): UseMutationReturn<
  Prompt,
  UpdatePromptArgs,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async ({ id, body }: UpdatePromptArgs) =>
      unwrap(
        await api.PUT('/api/prompts/{prompt_id}', {
          params: { path: { prompt_id: id } },
          body: { body },
        }),
      ),
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.prompts() })
    },
  })
}

/**
 * `useMutation` to delete a prompt (`DELETE /api/prompts/{id}`). Per
 * `docs/api.md` this deletes ALL versions of that `(project_id, name)` —
 * not just the targeted version row. 204 on success, 404 if unknown
 * (surfaced as an `ApiError`). On success we invalidate the broad
 * `keys.prompts()` prefix so the project's prompt list drops it.
 */
export function useDeletePromptMutation(): UseMutationReturn<
  void,
  number,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (id: number) => {
      unwrap(
        await api.DELETE('/api/prompts/{prompt_id}', {
          params: { path: { prompt_id: id } },
        }),
      )
    },
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.prompts() })
    },
  })
}

/**
 * `useQuery` for a side-effect-free run preview
 * (`GET /api/runs/{projectId}/preview`). The path segment is the
 * PROJECT id (no run exists yet); exactly one of `prompt_id` /
 * `prompt_body` is sent, plus an optional `phase`. By contract this
 * creates NO run row/event/dir (`docs/api.md`). `enabled` gates the
 * fetch so the wizard only previews on demand (reaching step 3).
 */
export function usePreviewQuery(
  selection: MaybeRefOrGetter<PreviewSelection | null>,
  enabled: MaybeRefOrGetter<boolean>,
): UseQueryReturn<Preview> {
  return useQuery({
    // Non-null assertion is safe: `enabled` is false (query disabled)
    // whenever the selection is null, so the key getter is only used
    // for an actually-runnable fetch.
    key: () => keys.preview(toValue(selection) as PreviewSelection),
    enabled: () => toValue(enabled) && toValue(selection) != null,
    query: async () => {
      const sel = toValue(selection) as PreviewSelection
      const query: { prompt_body?: string; prompt_id?: number; phase?: string } =
        'promptId' in sel.source
          ? { prompt_id: sel.source.promptId }
          : { prompt_body: sel.source.promptBody }
      if (sel.phase != null && sel.phase !== '') query.phase = sel.phase
      return unwrap(
        await api.GET('/api/runs/{run_id}/preview', {
          // `run_id` path segment carries the PROJECT id here (the API
          // nests preview under runs but it operates on a project —
          // `docs/api.md` / schema.d.ts comment).
          params: {
            path: { run_id: String(sel.projectId) },
            query,
          },
        }),
      )
    },
  })
}

/**
 * `useMutation` to create a run (`POST /api/runs`). The body carries
 * exactly one of `prompt_id` / `prompt_body` plus only the options the
 * caller set. On success it invalidates `keys.runs()` so every run list
 * / hub status reflects the new run.
 */
export function useCreateRunMutation(): UseMutationReturn<
  Run,
  RunCreate,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (body: RunCreate) =>
      unwrap(await api.POST('/api/runs', { body })),
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.runs() })
    },
  })
}

/**
 * `useQuery` for one run's full detail incl. `iters[]`
 * (`GET /api/runs/{run_id}`). This is the canonical Colada-cached run
 * resource the run-detail view (W4) and the iters pane (W5) read; W4's
 * SSE handler invalidates `keys.runDetail(id)` so status/iter changes
 * pushed over the stream re-fetch this.
 */
export function useRunDetailQuery(
  runId: MaybeRefOrGetter<string>,
): UseQueryReturn<RunDetail> {
  return useQuery({
    key: () => keys.runDetail(toValue(runId)),
    query: async () =>
      unwrap(
        await api.GET('/api/runs/{run_id}', {
          params: { path: { run_id: toValue(runId) } },
        }),
      ),
  })
}

/**
 * `useQuery` for a run's direct children (`GET /api/runs/{id}/children`).
 * Feeds the dashboard Children pane (spec.md §9.1, 9e). The events
 * store invalidates `keys.runChildren(runId)` when a `subagent_dispatch`,
 * `subagent_return`, or `child_runs_resolved` event lands on the parent's
 * SSE stream, so the pane refetches in lockstep with each lifecycle
 * transition. No polling; no per-child SSE.
 */
export function useRunChildrenQuery(
  runId: MaybeRefOrGetter<string>,
): UseQueryReturn<Run[]> {
  return useQuery({
    key: () => keys.runChildren(toValue(runId)),
    query: async () =>
      unwrap(
        await api.GET('/api/runs/{run_id}/children', {
          params: { path: { run_id: toValue(runId) } },
        }),
      ),
  })
}

/**
 * `useQuery` for a page of a run's persisted events
 * (`GET /api/runs/{run_id}/events?after_seq=&limit=&offset=`). W4 uses
 * this for the REPLAY path (a finished run: history is fetched over REST
 * and rendered statically — NO SSE; see `stores/events.ts`). For a live
 * run events arrive via SSE, not this query.
 */
export function useRunEventsQuery(
  runId: MaybeRefOrGetter<string>,
  page: MaybeRefOrGetter<EventPage>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<PaginatedEvents> {
  return useQuery({
    key: () => keys.runEvents(toValue(runId), toValue(page)),
    enabled: () => toValue(enabled),
    query: async () => {
      const p = toValue(page)
      return unwrap(
        await api.GET('/api/runs/{run_id}/events', {
          params: {
            path: { run_id: toValue(runId) },
            query: {
              after_seq: p.afterSeq,
              limit: p.limit,
              offset: p.offset,
            },
          },
        }),
      )
    },
  })
}

/**
 * `useMutation` to cancel a running run (`POST /api/runs/{id}/cancel`).
 * On success invalidates `keys.runDetail(id)` (the detail view refetches
 * the now-terminal status) and `keys.runs()` (every run list / hub
 * status reflects the cancellation).
 */
export function useCancelRunMutation(): UseMutationReturn<
  Run,
  string,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (runId: string) =>
      unwrap(
        await api.POST('/api/runs/{run_id}/cancel', {
          params: { path: { run_id: runId } },
        }),
      ),
    onSuccess: (data: Run) => {
      void cache.invalidateQueries({ key: keys.runDetail(data.id) })
      void cache.invalidateQueries({ key: keys.runs() })
    },
  })
}

/**
 * `useMutation` to delete a run (`DELETE /api/runs/{run_id}`). Cascade-
 * deletes the run's events / iters / descendants (DB-only — files on
 * disk are left alone, matching `DELETE /api/projects/{id}`). 204 on
 * success, 404 if unknown, 409 if the run is still active
 * (`running` / `awaiting_children`) — the latter two surface as an
 * `ApiError` carrying `status` so the caller can show an inline message
 * (e.g. skip the row in a bulk delete and report which ones refused).
 *
 * On success invalidates the broad `keys.runs()` prefix so every
 * run list / hub status / child pane drops the deleted row(s). The
 * detail key for the deleted run is dropped together with the list
 * (`['runs', 'detail', id]` is a prefix-descendant of `['runs']`).
 */
export function useDeleteRunMutation(): UseMutationReturn<
  void,
  string,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (runId: string) => {
      unwrap(
        await api.DELETE('/api/runs/{run_id}', {
          params: { path: { run_id: runId } },
        }),
      )
    },
    onSuccess: () => {
      void cache.invalidateQueries({ key: keys.runs() })
    },
  })
}

/**
 * `useMutation` to close a chat-mode run
 * (`POST /api/runs/{id}/close`; W3). The endpoint returns 409 if the
 * run is task-mode (chat-only close) or already terminal, and 404 if
 * unknown — both surface as an `ApiError` carrying `status`. On success
 * the run transitions to the `closed` terminal status; invalidate
 * `keys.runDetail(id)` (the chat view re-fetches and goes terminal)
 * and `keys.runs()` (every chat list / project view picks up the new
 * status).
 */
export function useCloseChatMutation(): UseMutationReturn<
  Run,
  string,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async (runId: string) =>
      unwrap(
        await api.POST('/api/runs/{run_id}/close', {
          params: { path: { run_id: runId } },
        }),
      ),
    onSuccess: (data: Run) => {
      void cache.invalidateQueries({ key: keys.runDetail(data.id) })
      void cache.invalidateQueries({ key: keys.runs() })
    },
  })
}

/** Arguments for the resume mutation: which run + the answer text. */
export interface ResumeRunArgs {
  runId: string
  answer: string
}

/**
 * `useMutation` to resume a paused run with an answer
 * (`POST /api/runs/{id}/resume` body `{answer}`). The API returns 409 if
 * the run is not paused / already running and 404 if unknown — both
 * surface as an `ApiError` carrying `status` so the form can show an
 * inline message. On success the run goes back to running; we invalidate
 * `keys.runDetail(id)` and `keys.runs()` so the new status propagates.
 */
export function useResumeRunMutation(): UseMutationReturn<
  Run,
  ResumeRunArgs,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async ({ runId, answer }: ResumeRunArgs) =>
      unwrap(
        await api.POST('/api/runs/{run_id}/resume', {
          params: { path: { run_id: runId } },
          body: { answer },
        }),
      ),
    onSuccess: (data: Run) => {
      void cache.invalidateQueries({ key: keys.runDetail(data.id) })
      void cache.invalidateQueries({ key: keys.runs() })
    },
  })
}

/**
 * `useQuery` for a directory listing (`GET /api/projects/{id}/files`).
 * `path` is '' for the project root; FileTree calls this per directory
 * as the user expands it (lazy fetch), so each directory is its own
 * cache entry under `keys.files(projectId)`. The backend already orders
 * entries dirs-first then name-asc — the caller MUST preserve that
 * order. The endpoint body is an untyped dict server-side; the response
 * is cast to {@link FileListing} (shape fixed by `docs/api.md`).
 */
export function useFileListingQuery(
  projectId: MaybeRefOrGetter<number>,
  path: MaybeRefOrGetter<string>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<FileListing> {
  return useQuery({
    key: () => keys.fileTree(toValue(projectId), toValue(path)),
    enabled: () => toValue(enabled),
    query: async () => {
      const p = toValue(path)
      return unwrap(
        await api.GET('/api/projects/{project_id}/files', {
          params: {
            path: { project_id: toValue(projectId) },
            // Omit `path` for the project root (backend default).
            query: p === '' ? {} : { path: p },
          },
        }),
      ) as FileListing
    },
  })
}

/**
 * `useQuery` for one file's raw content
 * (`GET /api/projects/{id}/files/{file_path}`). The viewer enables this
 * only once a file is selected. A binary file → 415, an oversized file
 * → 413, a sandbox violation → 400, an absent file → 404: all surface
 * as a thrown {@link ApiError} carrying `status` so FileViewer can show
 * the right friendly message (and the binary-download link). The body
 * is an untyped dict server-side; cast to {@link FileContent} (shape
 * fixed by `docs/api.md`).
 */
export function useFileContentQuery(
  projectId: MaybeRefOrGetter<number>,
  path: MaybeRefOrGetter<string | null>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<FileContent> {
  return useQuery({
    // Non-null assertion is safe: `enabled` gates the fetch off when
    // the path is null, so the key/query only run for a real path.
    key: () => keys.fileContent(toValue(projectId), toValue(path) ?? ''),
    enabled: () => toValue(enabled) && toValue(path) != null,
    query: async () =>
      unwrap(
        await api.GET('/api/projects/{project_id}/files/{file_path}', {
          params: {
            path: {
              project_id: toValue(projectId),
              file_path: toValue(path) as string,
            },
          },
        }),
      ) as FileContent,
  })
}

/**
 * Build the raw-content URL for a file (used as the binary-download
 * `href`; the browser fetches the bytes directly, bypassing the JSON
 * query path). Mirrors the `GET .../files/{file_path}` route; the path
 * is encoded segment-wise so sub-paths and spaces survive.
 */
export function fileRawUrl(projectId: number, path: string): string {
  const encoded = path
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
  return `/api/projects/${projectId}/files/${encoded}`
}

/**
 * `useQuery` for a run-artifacts directory listing
 * (`GET /api/runs/{run_id}/artifacts`; ADR-25). The exact analogue of
 * {@link useFileListingQuery} but scoped to a run's artifacts sandbox
 * (`data_dir/runs/<run_id>`). `path` is '' for the artifacts root;
 * FileTree calls this per directory on lazy-expand. The backend (a thin
 * adapter over the project file browser — ADR-25) already orders entries
 * dirs-first then name-asc; the caller preserves that order. A missing
 * run OR a run with no artifacts dir → 404 (surfaced as an
 * {@link ApiError} so the pane can show its empty state). The body is an
 * untyped dict server-side; cast to {@link FileListing} (shape fixed by
 * `docs/api.md`, identical to the project browser).
 */
export function useArtifactListingQuery(
  runId: MaybeRefOrGetter<string>,
  path: MaybeRefOrGetter<string>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<FileListing> {
  return useQuery({
    key: () => keys.artifactTree(toValue(runId), toValue(path)),
    enabled: () => toValue(enabled),
    query: async () => {
      const p = toValue(path)
      return unwrap(
        await api.GET('/api/runs/{run_id}/artifacts', {
          params: {
            path: { run_id: toValue(runId) },
            // Omit `path` for the artifacts root (backend default).
            query: p === '' ? {} : { path: p },
          },
        }),
      ) as FileListing
    },
  })
}

/**
 * `useQuery` for one artifact's raw content
 * (`GET /api/runs/{run_id}/artifacts/{file_path}`; ADR-25). The exact
 * analogue of {@link useFileContentQuery}. Binary → 415, oversized (>5
 * MiB) → 413, sandbox violation → 400, absent → 404: all surface as a
 * thrown {@link ApiError} carrying `status` so FileViewer shows the
 * right friendly message (and the binary-download link). The viewer
 * enables this only once an artifact is selected. The body is an untyped
 * dict server-side; cast to {@link FileContent} (shape fixed by
 * `docs/api.md`).
 */
export function useArtifactContentQuery(
  runId: MaybeRefOrGetter<string>,
  path: MaybeRefOrGetter<string | null>,
  enabled: MaybeRefOrGetter<boolean> = true,
): UseQueryReturn<FileContent> {
  return useQuery({
    // Non-null assertion is safe: `enabled` gates the fetch off when
    // the path is null, so the key/query only run for a real path.
    key: () => keys.artifactContent(toValue(runId), toValue(path) ?? ''),
    enabled: () => toValue(enabled) && toValue(path) != null,
    query: async () =>
      unwrap(
        await api.GET('/api/runs/{run_id}/artifacts/{file_path}', {
          params: {
            path: {
              run_id: toValue(runId),
              file_path: toValue(path) as string,
            },
          },
        }),
      ) as FileContent,
  })
}

/** Arguments for the artifact-write mutation (14c — ADR-40). */
export interface ArtifactWriteArgs {
  /** The paused run id (the PUT path segment). */
  runId: string
  /** Sandbox-relative artifact path; must equal the paused iter's
   *  `signal_args.review_path` or the server returns 409. */
  path: string
  /** New text content (UTF-8). NUL bytes → 415. >MAX_FILE_BYTES → 413. */
  content: string
  /** Optional editor tag for the audit event; server defaults to
   *  "dashboard" when omitted. */
  editor?: string
}

/** Shape of the 200 body returned by the artifact-write endpoint. */
export interface ArtifactWriteResult {
  path: string
  size: number
  sha256: string
}

/**
 * `useMutation` for `PUT /api/runs/{run_id}/artifacts/{file_path}` (14a's
 * endpoint, activated by 14b's `review_path` sentinel attribute — ADR-40).
 * Writes `content` to the artifact in-place during a paused review and
 * appends an `artifact_edited` event with pre/post SHA-256 hashes.
 *
 * Raw `fetch()` rather than the typed `api.PUT(...)`: the backend route
 * hand-parses `request.json()` instead of declaring a Pydantic body
 * model, so the generated OpenAPI op carries `requestBody?: never` and
 * openapi-fetch refuses a body field. Going through `fetch` keeps the
 * write path honest with the backend contract (the route is the single
 * write entry point per ADR-40 §B1) without touching the backend.
 *
 * On 4xx we throw an {@link ApiError} carrying status + parsed body
 * (404 unknown / 409 not-paused/no-review-path/path-mismatch / 413
 * too-large / 415 binary / 400 sandbox), so {@link PauseAnswerForm}
 * can surface the right inline message.
 *
 * On success we invalidate `keys.artifactContent(runId, path)` so the
 * editor's loaded baseline (and any sibling cache reader) sees the
 * post-save bytes.
 */
export function useArtifactWriteMutation(): UseMutationReturn<
  ArtifactWriteResult,
  ArtifactWriteArgs,
  ApiError
> {
  const cache = useQueryCache()
  return useMutation({
    mutation: async ({
      runId,
      path,
      content,
      editor,
    }: ArtifactWriteArgs) => {
      const url = artifactRawUrl(runId, path)
      const body: { content: string; editor?: string } = { content }
      if (editor != null) body.editor = editor
      const res = await fetch(url, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      })
      let parsed: unknown = null
      try {
        parsed = await res.json()
      } catch {
        // Empty / non-JSON body — leave `parsed` as null.
      }
      if (!res.ok) throw new ApiError(res.status, parsed)
      return parsed as ArtifactWriteResult
    },
    onSuccess: (_data, vars) => {
      void cache.invalidateQueries({
        key: keys.artifactContent(vars.runId, vars.path),
      })
    },
  })
}

/**
 * Build the raw-content URL for an artifact (the binary-download
 * `href`). Mirrors the `GET /api/runs/{run_id}/artifacts/{file_path}`
 * route; the path is encoded segment-wise so sub-paths and spaces
 * survive. The artifacts analogue of {@link fileRawUrl}.
 */
export function artifactRawUrl(runId: string, path: string): string {
  const encoded = path
    .split('/')
    .map((seg) => encodeURIComponent(seg))
    .join('/')
  return `/api/runs/${encodeURIComponent(runId)}/artifacts/${encoded}`
}

/**
 * The data-source abstraction the shared file browser
 * (FileTree/FileTreeNode/FileViewer) renders against. Both the W6
 * project file browser and the W7 run-artifacts pane render through the
 * SAME components — the only difference is which endpoint family +
 * ephemeral-UI-state instance is in play. Each concrete source
 * (`projectFileSource` / `runArtifactSource`) wires one endpoint family
 * (project files vs. run artifacts) behind this single interface so
 * there is exactly ONE tree + ONE viewer + ONE render pipeline (mirrors
 * ADR-25's single-sourced backend).
 *
 * The hook factories MUST be called from a component setup scope (they
 * call Colada `useQuery`); FileTreeNode/FileViewer call them exactly as
 * they previously called the bound `useFile*Query` hooks. `storeId`
 * keys the per-source ephemeral UI store instance (expanded dirs +
 * selection) so the project browser and an artifacts pane never share
 * selection/expansion state.
 */
export interface BrowserSource {
  /** Stable id for the per-source ephemeral UI store (see stores/files.ts). */
  readonly storeId: string
  /** Lazy directory-listing query for `path` ('' = sandbox root). */
  useListing(
    path: MaybeRefOrGetter<string>,
    enabled?: MaybeRefOrGetter<boolean>,
  ): UseQueryReturn<FileListing>
  /** File-content query for `path` (`null` ⇒ disabled / no selection). */
  useContent(
    path: MaybeRefOrGetter<string | null>,
    enabled?: MaybeRefOrGetter<boolean>,
  ): UseQueryReturn<FileContent>
  /** Raw-bytes download URL for `path` (binary fallback). */
  rawUrl(path: string): string
}

/**
 * The W6 project file-browser source: the sandboxed read-only browser
 * for a registered project (`/api/projects/{id}/files…`). Behaviour is
 * byte-for-byte the pre-W7 wiring — FileTree/FileViewer used to call
 * `useFileListingQuery`/`useFileContentQuery`/`fileRawUrl` directly with
 * this `projectId`; this just packages those same calls behind
 * {@link BrowserSource}.
 */
export function projectFileSource(projectId: number): BrowserSource {
  return {
    storeId: `project:${projectId}`,
    useListing: (path, enabled = true) =>
      useFileListingQuery(projectId, path, enabled),
    useContent: (path, enabled = true) =>
      useFileContentQuery(projectId, path, enabled),
    rawUrl: (path) => fileRawUrl(projectId, path),
  }
}

/**
 * The W7 run-artifacts source: the sandboxed read-only browser for a
 * run's artifacts dir (`/api/runs/{id}/artifacts…`; ADR-25). The exact
 * analogue of {@link projectFileSource} over the artifacts endpoint
 * family — the SAME FileTree/FileViewer render it.
 */
export function runArtifactSource(runId: string): BrowserSource {
  return {
    storeId: `run:${runId}`,
    useListing: (path, enabled = true) =>
      useArtifactListingQuery(runId, path, enabled),
    useContent: (path, enabled = true) =>
      useArtifactContentQuery(runId, path, enabled),
    rawUrl: (path) => artifactRawUrl(runId, path),
  }
}

/**
 * The shared cache-invalidation helper. W4's SSE handler and any
 * post-mutation flow call this with the broadest affected key prefix
 * (e.g. `invalidate(keys.projects())`, `invalidate(keys.runs())`). Pass
 * `exact` to invalidate only the precise key, not its descendants.
 *
 * Must be called from a component/composable setup scope (it resolves
 * the Colada query cache via `useQueryCache`).
 */
export function useInvalidate(): (
  key: readonly (string | number | object)[],
  exact?: boolean,
) => Promise<unknown> {
  const cache = useQueryCache()
  return (key, exact = false) =>
    cache.invalidateQueries({ key: key as unknown as string[], exact })
}

/**
 * A standalone loading/error/data view-model derived from a Colada
 * query, for `<AsyncBoundary>`. `isLoading` is true only on the first
 * load (pending with no data); background revalidations don't flip it.
 */
export function asAsyncState<T>(q: UseQueryReturn<T>): {
  isLoading: import('vue').ComputedRef<boolean>
  error: import('vue').ComputedRef<unknown>
} {
  return {
    isLoading: computed(() => q.isPending.value && q.data.value == null),
    error: computed(() => q.error.value),
  }
}
