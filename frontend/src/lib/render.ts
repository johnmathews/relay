// Client-side render pipeline: markdown + code highlighting + mermaid.
//
// Implemented in Phase 4 W6. The spec §9.4 pipeline:
//   markdown  → markdown-it + footnote/task-list plugins + tables
//   code      → shiki (lazy core + JS engine + per-lang grammars)
//   ```mermaid → mermaid.js inline SVG (dynamic import, first render)
//   plain/unknown → escaped monospace
//
// ── MANDATE 2 — shiki ────────────────────────────────────────────────
// The highlighter is built with `createHighlighterCore` from
// `shiki/core`, dynamically-imported per-language grammars, and the
// JavaScript regex engine from `@shikijs/engine-javascript`
// (`createJavaScriptRegexEngine`). The convenience bundle (the `shiki`
// default export, `getHighlighter`, `bundledLanguages`) is NEVER
// imported — it pulls every TextMate grammar (megabytes) into the main
// chunk and blows the <800KB-gz Phase 4 budget. Languages and themes
// are lazily `import()`ed on first use and cached.
//
// ── MANDATE 3 — mermaid ──────────────────────────────────────────────
// `mermaid` is loaded via a dynamic `import('mermaid')` ONLY on the
// first diagram render, then cached. There is NO static top-level
// `import mermaid`. A static import would force mermaid (large) into
// the initial bundle even for pages with no diagrams.
//
// ── Security ─────────────────────────────────────────────────────────
// Artifacts are agent-generated and untrusted. markdown-it is
// configured with `html: false` so embedded raw HTML is escaped, never
// executed (no `v-html` XSS vector). Code passed to the highlighter is
// tokenised by shiki (which HTML-escapes content); the plain/unknown
// fallback escapes manually. Mermaid renders into a detached container
// and we extract only the `<svg>` it produced.

import type { HighlighterCore } from 'shiki/core'

export interface RenderResult {
  /** Rendered, sanitised HTML safe to inject via `v-html`. */
  html: string
}

// ─────────────────────────────────────────────────────────────────────
// Shared helpers
// ─────────────────────────────────────────────────────────────────────

/** HTML-escape a string for safe interpolation into markup. */
export function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;')
}

/** Wrap source in an escaped monospace `<pre>` (plain/unknown fallback). */
function plainPre(source: string): string {
  return `<pre class="render-plain"><code>${escapeHtml(source)}</code></pre>`
}

// ─────────────────────────────────────────────────────────────────────
// Code highlighting — MANDATE 2
// ─────────────────────────────────────────────────────────────────────

/**
 * Languages we support. Each maps to a lazy grammar import; an unknown
 * language is rendered as escaped monospace (never throws). The seven
 * required by the Phase-4 verification (python/typescript/vue/bash/sql/
 * json/yaml) are all present. `markdown-it` fence info strings vary, so
 * a few common aliases are normalised.
 */
const LANG_LOADERS: Record<string, () => Promise<{ default: unknown }>> = {
  python: () => import('@shikijs/langs/python'),
  typescript: () => import('@shikijs/langs/typescript'),
  vue: () => import('@shikijs/langs/vue'),
  bash: () => import('@shikijs/langs/bash'),
  sql: () => import('@shikijs/langs/sql'),
  json: () => import('@shikijs/langs/json'),
  yaml: () => import('@shikijs/langs/yaml'),
}

/** Fence-info aliases → a canonical key in {@link LANG_LOADERS}. */
const LANG_ALIASES: Record<string, string> = {
  py: 'python',
  ts: 'typescript',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  yml: 'yaml',
}

const LIGHT_THEME = 'github-light'
const DARK_THEME = 'github-dark'

/**
 * Singleton core highlighter. Built once with NO languages/themes; both
 * are streamed in lazily via `loadLanguage` / `loadTheme` so the main
 * bundle never carries a grammar. The promise is cached so concurrent
 * callers share one instance.
 */
let highlighterPromise: Promise<HighlighterCore> | null = null

async function getHighlighter(): Promise<HighlighterCore> {
  if (highlighterPromise === null) {
    highlighterPromise = (async () => {
      const { createHighlighterCore } = await import('shiki/core')
      const { createJavaScriptRegexEngine } = await import(
        '@shikijs/engine-javascript'
      )
      return createHighlighterCore({
        // Themes/langs intentionally empty — loaded on demand below.
        themes: [],
        langs: [],
        engine: createJavaScriptRegexEngine(),
      })
    })()
  }
  return highlighterPromise
}

/** Themes already streamed into the core highlighter. */
const loadedThemes = new Set<string>()
/** Languages already streamed into the core highlighter. */
const loadedLangs = new Set<string>()

const THEME_LOADERS: Record<string, () => Promise<{ default: unknown }>> = {
  [LIGHT_THEME]: () => import('@shikijs/themes/github-light'),
  [DARK_THEME]: () => import('@shikijs/themes/github-dark'),
}

async function ensureThemes(hl: HighlighterCore): Promise<void> {
  for (const name of [LIGHT_THEME, DARK_THEME]) {
    if (loadedThemes.has(name)) continue
    const mod = await THEME_LOADERS[name]()
    // shiki accepts a ThemeRegistration; the lang/theme module default
    // export is exactly that shape. Cast is required because the loader
    // map is typed structurally (no `any`).
    await hl.loadTheme(mod.default as Parameters<HighlighterCore['loadTheme']>[0])
    loadedThemes.add(name)
  }
}

/**
 * Resolve a fence-info language token to a supported key, or `null` if
 * unsupported (caller falls back to escaped monospace).
 */
function resolveLang(lang: string): string | null {
  const norm = lang.trim().toLowerCase()
  const canonical = LANG_ALIASES[norm] ?? norm
  return canonical in LANG_LOADERS ? canonical : null
}

/**
 * Highlight a code block to HTML. Follows MANDATE 2: lazy core + JS
 * regex engine + per-language dynamic grammar import (cached). An
 * unsupported/unknown language falls back to escaped monospace and
 * never throws. Emits a dual-theme block (`--shiki-dark` CSS var) so a
 * dark mode can be added later without re-highlighting.
 */
export async function renderCode(
  source: string,
  lang: string,
): Promise<RenderResult> {
  const canonical = resolveLang(lang)
  if (canonical === null) {
    return { html: plainPre(source) }
  }
  try {
    const hl = await getHighlighter()
    await ensureThemes(hl)
    if (!loadedLangs.has(canonical)) {
      const mod = await LANG_LOADERS[canonical]()
      await hl.loadLanguage(
        mod.default as Parameters<HighlighterCore['loadLanguage']>[0],
      )
      loadedLangs.add(canonical)
    }
    return {
      html: hl.codeToHtml(source, {
        lang: canonical,
        themes: { light: LIGHT_THEME, dark: DARK_THEME },
        defaultColor: 'light',
      }),
    }
  } catch {
    // Highlighting must never break the viewer — degrade to monospace.
    return { html: plainPre(source) }
  }
}

// ─────────────────────────────────────────────────────────────────────
// Mermaid — MANDATE 3
// ─────────────────────────────────────────────────────────────────────

/**
 * Cached mermaid module promise. Populated by the FIRST `renderMermaid`
 * call via dynamic `import('mermaid')` — there is deliberately no
 * top-level static import (MANDATE 3). `initialize` is called once.
 */
let mermaidPromise: Promise<typeof import('mermaid').default> | null = null

async function getMermaid(): Promise<typeof import('mermaid').default> {
  if (mermaidPromise === null) {
    mermaidPromise = (async () => {
      const mod = await import('mermaid')
      mod.default.initialize({
        startOnLoad: false,
        securityLevel: 'strict',
        theme: 'default',
      })
      return mod.default
    })()
  }
  return mermaidPromise
}

let mermaidIdCounter = 0

/**
 * Render a mermaid diagram source to inline SVG. Follows MANDATE 3
 * (dynamic import on first render, cached). On a parse/render error the
 * raw source is shown in an escaped monospace block with an error note
 * — the pane never crashes.
 */
export async function renderMermaid(source: string): Promise<RenderResult> {
  try {
    const mermaid = await getMermaid()
    const id = `relay-mermaid-${++mermaidIdCounter}`
    const { svg } = await mermaid.render(id, source)
    return { html: `<div class="render-mermaid">${svg}</div>` }
  } catch (err) {
    const note = err instanceof Error ? err.message : 'diagram render failed'
    return {
      html:
        `<div class="render-mermaid-error">` +
        `<p class="render-mermaid-error__note">Mermaid: ${escapeHtml(note)}</p>` +
        `${plainPre(source)}</div>`,
    }
  }
}

// ─────────────────────────────────────────────────────────────────────
// Markdown — markdown-it + plugins
// ─────────────────────────────────────────────────────────────────────

// No `@types/markdown-it` is installed and adding a types dep is not
// allowed. markdown-it ships its own ESM with bundled `.d.ts` in recent
// versions, but to stay independent of that we use a narrow local
// structural type covering exactly the surface we touch. This avoids
// `any` (an explicit, commented type contract instead).
interface MarkdownItRenderer {
  rules: Record<
    string,
    | ((
        tokens: MdToken[],
        idx: number,
        opts: unknown,
        env: unknown,
        self: MarkdownItRenderer,
      ) => string)
    | undefined
  >
  renderToken(tokens: MdToken[], idx: number, opts: unknown): string
}
interface MdToken {
  type: string
  tag: string
  info: string
  content: string
  block: boolean
}
interface MarkdownItInstance {
  use(plugin: unknown, ...args: unknown[]): MarkdownItInstance
  render(src: string): string
  renderer: MarkdownItRenderer
  utils: { escapeHtml(s: string): string }
}
type MarkdownItCtor = new (opts?: Record<string, unknown>) => MarkdownItInstance

// A placeholder emitted for each code fence by the synchronous
// markdown-it pass; a second async pass replaces each with the real
// shiki/mermaid output. This is what lets a Vue component `await` the
// async highlight while markdown-it itself stays synchronous.
const FENCE_OPEN = ' RELAY_FENCE_'
const FENCE_CLOSE = ' '

let mdPromise: Promise<MarkdownItInstance> | null = null

async function getMarkdownIt(): Promise<MarkdownItInstance> {
  if (mdPromise === null) {
    mdPromise = (async () => {
      const MarkdownIt = (await import('markdown-it'))
        .default as unknown as MarkdownItCtor
      const footnote = (await import('markdown-it-footnote')).default
      const taskLists = (await import('markdown-it-task-lists')).default
      const md = new MarkdownIt({
        // SECURITY: html:false → raw HTML in untrusted agent artifacts
        // is escaped, not parsed. linkify off (no auto-link rewriting),
        // typographer off (deterministic output for tests).
        html: false,
        linkify: false,
        typographer: false,
      })
      md.use(footnote)
      // Render task list checkboxes as disabled inputs (read-only view).
      md.use(taskLists, { enabled: false, label: true })
      // Replace fence rendering with a placeholder the async pass fills.
      md.renderer.rules.fence = (tokens, idx): string => {
        const t = tokens[idx]
        const lang = (t.info || '').trim().split(/\s+/)[0] ?? ''
        const payload = JSON.stringify({ lang, code: t.content })
        // base64 so the JSON (which may contain markdown specials and
        // the NUL sentinel boundaries) survives untouched in the HTML.
        const b64 =
          typeof btoa === 'function'
            ? btoa(unescape(encodeURIComponent(payload)))
            : Buffer.from(payload, 'utf-8').toString('base64')
        return `${FENCE_OPEN}${b64}${FENCE_CLOSE}`
      }
      return md
    })()
  }
  return mdPromise
}

function decodeB64(b64: string): string {
  if (typeof atob === 'function') {
    return decodeURIComponent(escape(atob(b64)))
  }
  return Buffer.from(b64, 'base64').toString('utf-8')
}

/**
 * Render a markdown document to sanitised HTML (spec §9.4).
 *
 * - tables (markdown-it core), task lists, footnotes are enabled
 * - raw HTML is escaped (`html:false`) — no XSS via agent artifacts
 * - ```mermaid fences → inline SVG via {@link renderMermaid}
 * - other ```lang fences → shiki-highlighted via {@link renderCode}
 *
 * Two-pass: markdown-it renders synchronously with each fence emitted
 * as an opaque placeholder, then this function resolves the (async)
 * code/mermaid renderers and substitutes their HTML. Callers `await`
 * the returned promise; the consuming component injects the result.
 */
export async function renderMarkdown(source: string): Promise<RenderResult> {
  const md = await getMarkdownIt()
  const rendered = md.render(source)

  // Collect every placeholder and resolve fences concurrently.
  const re = new RegExp(`${FENCE_OPEN}([A-Za-z0-9+/=]+)${FENCE_CLOSE}`, 'g')
  const jobs: Promise<{ token: string; html: string }>[] = []
  const seen = new Set<string>()
  for (const m of rendered.matchAll(re)) {
    const token = m[0]
    if (seen.has(token)) continue
    seen.add(token)
    const { lang, code } = JSON.parse(decodeB64(m[1])) as {
      lang: string
      code: string
    }
    jobs.push(
      (async () => {
        if (lang.trim().toLowerCase() === 'mermaid') {
          return { token, html: (await renderMermaid(code)).html }
        }
        if (lang.trim() === '') {
          return { token, html: plainPre(code) }
        }
        return { token, html: (await renderCode(code, lang)).html }
      })(),
    )
  }
  let html = rendered
  for (const { token, html: fenceHtml } of await Promise.all(jobs)) {
    html = html.split(token).join(fenceHtml)
  }
  return { html }
}

// ─────────────────────────────────────────────────────────────────────
// Diff — diff2html
// ─────────────────────────────────────────────────────────────────────
//
// DECISION (W6): keep `diff2html` (NOT v-code-diff).
// Rationale: `docs/spec.md` §9.4 and `docs/plan.md` both prescribe
// diff2html; it is already a pinned dependency (`^3.4`); this is a
// single-user localhost MVP where the "maintenance-inactive" concern
// (raised in the Phase-4 scope discussion, mandate 5) carries little
// weight — there is no untrusted multi-tenant exposure and the pinned
// version works. We hit NO concrete integration blocker: diff2html's
// `html()` API produces standalone markup we render via a thin wrapper,
// and we generate the unified diff ourselves (no `diff` dep needed) so
// the only diff2html surface used is its stable HTML formatter. Adding
// v-code-diff would mean a new dependency and a spec deviation for no
// MVP benefit. Revisit only if diff2html breaks on a dependency bump.

/** Whether the rendered diff is unified or side-by-side. */
export type DiffStyle = 'line-by-line' | 'side-by-side'

/**
 * Build a minimal unified-diff patch from two file revisions using a
 * classic LCS. Good enough for the MVP file/artifact compare; diff2html
 * only needs a valid unified diff to format.
 */
function unifiedDiff(
  oldText: string,
  newText: string,
  filename: string,
): string {
  const a = oldText.split('\n')
  const b = newText.split('\n')
  const n = a.length
  const m = b.length
  // LCS length table.
  const lcs: number[][] = Array.from({ length: n + 1 }, () =>
    new Array<number>(m + 1).fill(0),
  )
  for (let i = n - 1; i >= 0; i--) {
    for (let j = m - 1; j >= 0; j--) {
      lcs[i][j] =
        a[i] === b[j]
          ? lcs[i + 1][j + 1] + 1
          : Math.max(lcs[i + 1][j], lcs[i][j + 1])
    }
  }
  // Backtrack into a sequence of ' '/'-'/'+' lines.
  const lines: string[] = []
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) {
      lines.push(` ${a[i]}`)
      i++
      j++
    } else if (lcs[i + 1][j] >= lcs[i][j + 1]) {
      lines.push(`-${a[i]}`)
      i++
    } else {
      lines.push(`+${b[j]}`)
      j++
    }
  }
  for (; i < n; i++) lines.push(`-${a[i]}`)
  for (; j < m; j++) lines.push(`+${b[j]}`)

  const header =
    `--- a/${filename}\n` +
    `+++ b/${filename}\n` +
    `@@ -1,${n} +1,${m} @@\n`
  return header + lines.join('\n') + '\n'
}

/**
 * Render a unified or side-by-side diff between two revisions of a file
 * (spec §9.4 diff). diff2html is dynamically imported so its formatter
 * stays out of the main bundle (only loaded when a compare is shown).
 * Returns sanitised HTML (diff2html escapes content).
 */
export async function renderDiff(
  oldText: string,
  newText: string,
  filename: string,
  style: DiffStyle = 'side-by-side',
): Promise<RenderResult> {
  const patch = unifiedDiff(oldText, newText, filename)
  const { html: d2hHtml } = await import('diff2html')
  const html = d2hHtml(patch, {
    drawFileList: false,
    matching: 'lines',
    outputFormat: style,
  })
  return { html: `<div class="render-diff">${html}</div>` }
}
