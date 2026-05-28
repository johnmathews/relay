import { describe, it, expect, beforeEach, vi } from 'vitest'
import { mount, config } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { createRouter, createMemoryHistory } from 'vue-router'
import TimelinePane from '../src/components/runs/TimelinePane.vue'
import type { StreamEvent } from '../src/stores/events'
import { useBrowserUiStore } from '../src/stores/files'

// Realistic relay event kinds + payloads (spec §3.2 / src/relay_v2/
// events.py / orchestrator/loop.py — NOT invented).
const MIXED: StreamEvent[] = [
  { seq: 1, kind: 'run_started', payload: { project_id: 1, max_iters: 5 } },
  { seq: 2, kind: 'iter_started', payload: { seq: 1, phase: 'evaluate' } },
  {
    seq: 3,
    kind: 'assistant_text',
    payload: { text: 'Looking at the code.', turn_seq: 0, kind: 'text' },
  },
  {
    seq: 4,
    kind: 'tool_use_start',
    payload: { tool_id: 't1', name: 'Bash', args: { command: 'ls' } },
  },
  {
    seq: 5,
    kind: 'tool_use_end',
    payload: {
      tool_id: 't1',
      result: { stdout: 'a\nb' },
      is_error: false,
      duration_ms: 12,
    },
  },
  {
    seq: 6,
    kind: 'signal_emit',
    payload: { kind: 'phase_start', args: { phase: 'plan' } },
  },
  { seq: 7, kind: 'iter_ended', payload: { seq: 1, exit_reason: 'signal' } },
  { seq: 8, kind: 'run_ended', payload: { status: 'done', summary: 'ok' } },
  { seq: 9, kind: 'some_future_kind', payload: { whatever: true } },
]

describe('TimelinePane', () => {
  // Shared router ref — re-created per test so each test gets a clean
  // history. Exposed at describe scope so the artifact_edited click
  // tests can inspect currentRoute after triggering the click.
  let testRouter: ReturnType<typeof createRouter>

  beforeEach(() => {
    // TimelinePane now reads `useTimelinePrefsStore` for per-type
    // expand defaults (2026-05-25). Every mount needs an active
    // Pinia or the store factory throws "no active Pinia".
    setActivePinia(createPinia())
    localStorage.removeItem('relay.timeline.expanded')
    // TimelinePane calls useRouter() + useRoute() (Phase 1 fix —
    // onArtifactEditedClick pushes ?view=artifact:<path>). Every mount
    // needs a router plugin or vue-router throws "No active router".
    // A minimal memory-history router with a catch-all is sufficient.
    testRouter = createRouter({
      history: createMemoryHistory(),
      routes: [{ path: '/:pathMatch(.*)*', component: { template: '<div/>' } }],
    })
    config.global.plugins = [testRouter]
  })

  it('renders all mixed event types readably', () => {
    const w = mount(TimelinePane, { props: { events: MIXED } })
    const text = w.text()
    expect(w.find('[data-testid="tool-call-card"]').exists()).toBe(true)
    expect(w.find('[data-testid="signal-card"]').exists()).toBe(true)
    expect(text).toContain('Looking at the code.')
    expect(text).toContain('Bash')
    // Tool start+end merged into one card.
    expect(w.findAll('[data-testid="tool-call-card"]').length).toBe(1)
    // Boundary + unknown kinds still render (never throws).
    expect(w.find('[data-kind="run_ended"]').exists()).toBe(true)
    expect(w.find('[data-kind="some_future_kind"]').exists()).toBe(true)
  })

  it('renders pendingTurns as in-progress pseudo-rows after canonical events', () => {
    // ADR-46 Plan B: the dashboard shows tokens-as-they-arrive by
    // accumulating assistant_delta SSE frames into a pendingTurns
    // buffer (events store). TimelinePane renders one pseudo-row per
    // pending entry below the canonical timeline; the rows are
    // distinct (data-testid="pending-turn") so the canonical
    // assistant_text card replaces them cleanly when it lands.
    const w = mount(TimelinePane, {
      props: {
        events: [
          { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
        ],
        pendingTurns: [
          {
            iterId: 20,
            turnSeq: 1,
            kind: 'thinking' as const,
            text: 'let me look',
          },
          {
            iterId: 20,
            turnSeq: 1,
            kind: 'text' as const,
            text: 'hello…',
          },
        ],
      },
    })
    const pending = w.findAll('[data-testid="pending-turn"]')
    expect(pending.length).toBe(2)
    expect(pending[0]!.attributes('data-pending-kind')).toBe('thinking')
    expect(pending[0]!.text()).toContain('let me look')
    expect(pending[1]!.text()).toContain('hello…')
  })

  it('hides pendingTurns when an iter filter is active', () => {
    // Pending rows only stream for the *currently running* iter (deltas
    // come from the live pi process). When the user is zoomed into a
    // specific iter view (selectedIterSeq set), showing the live
    // deltas of a different iter is confusing — hide them entirely.
    const w = mount(TimelinePane, {
      props: {
        events: [
          { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
          { seq: 2, kind: 'iter_ended', payload: { seq: 1, exit_reason: 'signal' } },
          { seq: 3, kind: 'iter_started', payload: { seq: 2, phase: null } },
        ],
        selectedIterSeq: 1,
        pendingTurns: [
          { iterId: 21, turnSeq: 1, kind: 'text' as const, text: 'iter 2 live' },
        ],
      },
    })
    expect(w.findAll('[data-testid="pending-turn"]').length).toBe(0)
  })

  it('renders harness_session_ended as a UsageRow (ADR-39)', () => {
    const events: StreamEvent[] = [
      { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
      {
        seq: 2,
        kind: 'harness_session_ended',
        payload: {
          stop_reason: 'clean',
          summary: 'ok',
          messages: [
            { role: 'assistant', usage: { input: 5, output: 3 } },
          ],
        },
      },
      {
        seq: 3,
        kind: 'iter_ended',
        payload: { seq: 1, signal_kind: 'done', exit_reason: 'signal' },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    // UsageRow renders the stop_reason badge text + token totals.
    expect(w.text()).toContain('clean')
    // Row uses the 'usage' data-kind branch, not the generic fallback.
    expect(w.find('[data-kind="harness_session_ended"]').exists()).toBe(true)
    // The .usage-row class is unique to UsageRow.vue.
    expect(w.find('.usage-row').exists()).toBe(true)
    expect(w.find('.usage-row').text()).toContain('5')
    expect(w.find('.usage-row').text()).toContain('3')
  })

  it('renders artifact_edited as a one-line row (14c — ADR-40)', () => {
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'artifact_edited',
        payload: {
          path: 'improvement-plan.md',
          size_before: 11,
          size_after: 14,
          sha256_before: 'a3f25c…',
          sha256_after: '9b1e2d…',
          editor: 'dashboard',
        },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const row = w.find('[data-testid="artifact-edited-row"]')
    expect(row.exists()).toBe(true)
    const text = row.text()
    expect(text).toContain('improvement-plan.md')
    // Short sha rendering: first 4 chars + ellipsis.
    expect(text).toContain('a3f2…')
    expect(text).toContain('9b1e…')
    expect(text).toContain('dashboard')
    // Row uses its own data-kind, not the generic fallback.
    expect(w.find('[data-kind="artifact_edited"]').exists()).toBe(true)
  })

  it('artifact_edited with null sha256_before renders ∅ → after (create)', () => {
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'artifact_edited',
        payload: {
          path: 'discussions/notes.md',
          size_before: 0,
          size_after: 25,
          sha256_before: null,
          sha256_after: 'deadbe…',
          editor: 'dashboard',
        },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const row = w.find('[data-testid="artifact-edited-row"]')
    expect(row.exists()).toBe(true)
    const text = row.text()
    expect(text).toContain('discussions/notes.md')
    expect(text).toContain('∅')
    // shortSha truncates to first 4 chars + ellipsis.
    expect(text).toContain('dead…')
  })

  it('artifact_edited row is a clickable target that selects the artifact and pushes ?view=artifact (14e + Phase 1 fix)', async () => {
    setActivePinia(createPinia())
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'artifact_edited',
        payload: {
          path: 'improvement-plan.md',
          size_before: 11,
          size_after: 14,
          sha256_before: 'a3f2',
          sha256_after: '9b1e',
          editor: 'dashboard',
        },
      },
    ]
    const w = mount(TimelinePane, {
      props: { events, runId: 'run-abc' },
    })
    const row = w.find('[data-testid="artifact-edited-row"]')
    expect(row.exists()).toBe(true)
    // The row renders as a <button> for keyboard + a11y affordance.
    expect((row.element as HTMLElement).tagName).toBe('BUTTON')
    await row.trigger('click')
    // Store selection still works.
    const store = useBrowserUiStore('run:run-abc')
    expect(store.selectedPath).toBe('improvement-plan.md')
    // Phase 1 fix: router must have pushed ?view=artifact:<path> so the
    // right pane opens the file viewer (ArtifactsPane was deleted in
    // Phase 1 — the old [data-testid="artifacts-pane"] selector was a
    // silent no-op; the sidebar-artifacts-section scroll target +
    // router push is the correct post-Phase-1 behavior).
    await vi.waitFor(() => {
      expect(testRouter.currentRoute.value.query.view).toBe(
        'artifact:improvement-plan.md',
      )
    })
  })

  it('create-path artifact_edited row is also clickable and pushes ?view=artifact (14e + Phase 1 fix)', async () => {
    setActivePinia(createPinia())
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'artifact_edited',
        payload: {
          path: 'discussions/notes.md',
          size_before: 0,
          size_after: 25,
          sha256_before: null,
          sha256_after: 'deadbe',
          editor: 'dashboard',
        },
      },
    ]
    const w = mount(TimelinePane, {
      props: { events, runId: 'run-xyz' },
    })
    const row = w.find('[data-testid="artifact-edited-row"]')
    await row.trigger('click')
    expect(useBrowserUiStore('run:run-xyz').selectedPath).toBe(
      'discussions/notes.md',
    )
    await vi.waitFor(() => {
      expect(testRouter.currentRoute.value.query.view).toBe(
        `artifact:${encodeURIComponent('discussions/notes.md')}`,
      )
    })
  })

  it('signal row has the distinctive card + a linkable anchor id', () => {
    const w = mount(TimelinePane, { props: { events: MIXED } })
    const sig = w.find('[data-testid="signal-card"]')
    expect(sig.exists()).toBe(true)
    expect(sig.attributes('id')).toBe('signal-6')
    expect(sig.find('a.signal-card__anchor').attributes('href')).toBe(
      '#signal-6',
    )
  })

  it('tool-call card collapses >5 lines and "show full" expands', async () => {
    const big = Array.from({ length: 20 }, (_, i) => `line ${i}`)
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'tool_use_start',
        payload: { tool_id: 't', name: 'Bash', args: { lines: big } },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const block = w.find('.tool-card__block')
    const collapsed = block.text()
    expect(collapsed.split('\n').length).toBeLessThanOrEqual(5)

    const toggle = w.find('[data-testid="tool-card-toggle"]')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    expect(w.find('.tool-card__block').text().split('\n').length).toBeGreaterThan(
      5,
    )
  })

  it('gear button opens a popover with per-type toggles; clicking flips defaults', async () => {
    // The popover lists the 5 collapsible types and reflects the
    // current type-default state. Clicking a row's toggle flips the
    // type default in the store (persisted via the store's own
    // localStorage watch). A second open is read-from-store, so a
    // collapsed-by-default type becomes expanded for all matching
    // rows on the next render.
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'tool_use_start',
        payload: { tool_id: 't', name: 'Bash', args: { x: 1 } },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const row = w.get('.timeline__row')
    expect(row.classes()).toContain('timeline__row--collapsed')

    // Popover hidden by default.
    expect(w.find('[data-testid="display-popover"]').exists()).toBe(false)
    // Click the gear to open.
    await w.get('[data-testid="display-gear"]').trigger('click')
    expect(w.find('[data-testid="display-popover"]').exists()).toBe(true)

    // Flip the `tool` type default to expanded.
    await w.get('[data-testid="display-toggle-tool"]').trigger('click')
    expect(row.classes()).not.toContain('timeline__row--collapsed')

    // Close the popover by clicking the gear again.
    await w.get('[data-testid="display-gear"]').trigger('click')
    expect(w.find('[data-testid="display-popover"]').exists()).toBe(false)
  })

  it('classifies assistant_text by payload.kind: text=assistant, thinking=thinking', () => {
    // ADR-18 keeps assistant `text` vs `thinking` distinct at the
    // protocol level; the dashboard now distinguishes them in the
    // row classifier so the type-default expand prefs and the
    // ASSISTANT highlight can target only the user-facing reply.
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'assistant_text',
        payload: { text: 'reasoning here', turn_seq: 1, kind: 'thinking' },
      },
      {
        seq: 2,
        kind: 'assistant_text',
        payload: { text: 'the answer', turn_seq: 1, kind: 'text' },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const rows = w.findAll('.timeline__row')
    expect(rows[0]!.attributes('data-row-type')).toBe('thinking')
    expect(rows[1]!.attributes('data-row-type')).toBe('assistant')
  })

  it('assistant row has the highlight class; thinking and tool do not', () => {
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'assistant_text',
        payload: { text: 'reply', turn_seq: 1, kind: 'text' },
      },
      {
        seq: 2,
        kind: 'assistant_text',
        payload: { text: 'inner monologue', turn_seq: 1, kind: 'thinking' },
      },
      {
        seq: 3,
        kind: 'tool_use_start',
        payload: { tool_id: 't', name: 'Bash', args: { x: 1 } },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const rows = w.findAll('.timeline__row')
    expect(rows[0]!.classes()).toContain('timeline__row--assistant')
    expect(rows[1]!.classes()).not.toContain('timeline__row--assistant')
    expect(rows[2]!.classes()).not.toContain('timeline__row--assistant')
  })

  it('assistant rows expanded by default; tool/thinking/signal collapsed', () => {
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'assistant_text',
        payload: { text: 'reply', turn_seq: 1, kind: 'text' },
      },
      {
        seq: 2,
        kind: 'assistant_text',
        payload: { text: 'inner', turn_seq: 1, kind: 'thinking' },
      },
      {
        seq: 3,
        kind: 'tool_use_start',
        payload: { tool_id: 't', name: 'Bash', args: { x: 1 } },
      },
      {
        seq: 4,
        kind: 'signal_emit',
        payload: { kind: 'phase_start', args: { phase: 'plan' } },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const rows = w.findAll('.timeline__row')
    expect(rows[0]!.classes()).not.toContain('timeline__row--collapsed') // assistant
    expect(rows[1]!.classes()).toContain('timeline__row--collapsed') // thinking
    expect(rows[2]!.classes()).toContain('timeline__row--collapsed') // tool
    expect(rows[3]!.classes()).toContain('timeline__row--collapsed') // signal
  })

  it('per-row toggle overrides the type default', async () => {
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'tool_use_start',
        payload: { tool_id: 't', name: 'Bash', args: { x: 1 } },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    const row = w.get('.timeline__row')
    expect(row.classes()).toContain('timeline__row--collapsed')

    // Click the toggle — this row expands; the type default
    // (collapsed for tools) is unaffected for any other tool row.
    await w.get('[data-testid="toggle-step"]').trigger('click')
    expect(row.classes()).not.toContain('timeline__row--collapsed')

    // Click again — collapse this row.
    await w.get('[data-testid="toggle-step"]').trigger('click')
    expect(row.classes()).toContain('timeline__row--collapsed')
  })

  it('copy button copies the row text to the clipboard', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'assistant_text',
        payload: { text: 'hello world', turn_seq: 1, kind: 'text' },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    await w.get('[data-testid="copy-step"]').trigger('click')
    expect(writeText).toHaveBeenCalledWith('hello world')
  })

  it('copy on a tool row includes both args and result JSON', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.assign(navigator, { clipboard: { writeText } })
    const events: StreamEvent[] = [
      {
        seq: 1,
        kind: 'tool_use_start',
        payload: { tool_id: 't', name: 'Bash', args: { command: 'ls -la' } },
      },
      {
        seq: 2,
        kind: 'tool_use_end',
        payload: { tool_id: 't', result: { stdout: 'a\nb' }, is_error: false, duration_ms: 5 },
      },
    ]
    const w = mount(TimelinePane, { props: { events } })
    await w.get('[data-testid="copy-step"]').trigger('click')
    const arg = writeText.mock.calls[0]![0] as string
    expect(arg).toContain('args:')
    expect(arg).toContain('"command": "ls -la"')
    expect(arg).toContain('result:')
    expect(arg).toContain('"stdout"')
  })

  it('pinned-to-bottom: appending an event auto-scrolls to the new tail', async () => {
    // The timeline is a live feed. When the user is already at the
    // bottom (within a small tolerance) new events must auto-scroll
    // the container into view. When they have scrolled up to read
    // history the auto-scroll MUST NOT yank them away.
    const w = mount(TimelinePane, {
      props: {
        events: [
          { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
        ],
      },
    })
    const scrollEl = w.get('.timeline').element as HTMLElement

    // Pretend the container has been scrolled to the bottom. jsdom
    // does not implement layout, so we plant the geometry directly.
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(scrollEl, 'clientHeight', { configurable: true, value: 400 })
    scrollEl.scrollTop = 600 // == scrollHeight - clientHeight ⇒ pinned
    await scrollEl.dispatchEvent(new Event('scroll'))

    // Append a new event. The pane should advance scrollTop to the
    // new bottom (jsdom: programmatic assignment is observable).
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, value: 1400 })
    await w.setProps({
      events: [
        { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
        { seq: 2, kind: 'assistant_text', payload: { text: 'hi', turn_seq: 1, kind: 'text' } },
      ],
    })
    // nextTick + the watch firing requires one more flush.
    await w.vm.$nextTick()
    expect(scrollEl.scrollTop).toBe(1000) // 1400 - 400 = new bottom
  })

  it('unpinned: scrolling up before a new event prevents auto-scroll + shows jump button', async () => {
    const w = mount(TimelinePane, {
      props: {
        events: [
          { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
        ],
      },
    })
    const scrollEl = w.get('.timeline').element as HTMLElement

    // User scrolled up — well above the bottom.
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, value: 1000 })
    Object.defineProperty(scrollEl, 'clientHeight', { configurable: true, value: 400 })
    scrollEl.scrollTop = 100
    await scrollEl.dispatchEvent(new Event('scroll'))

    // Jump-to-latest button appears when unpinned.
    expect(w.find('[data-testid="jump-to-latest"]').exists()).toBe(true)

    // Append a new event. scrollTop must NOT be moved (the user is
    // reading history; auto-scroll would yank them).
    Object.defineProperty(scrollEl, 'scrollHeight', { configurable: true, value: 1400 })
    await w.setProps({
      events: [
        { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
        { seq: 2, kind: 'assistant_text', payload: { text: 'hi', turn_seq: 1, kind: 'text' } },
      ],
    })
    await w.vm.$nextTick()
    expect(scrollEl.scrollTop).toBe(100)

    // Clicking the button re-pins and scrolls to the new tail.
    await w.get('[data-testid="jump-to-latest"]').trigger('click')
    expect(scrollEl.scrollTop).toBe(1000)
  })

  it('jump-to-latest button is hidden while pinned', async () => {
    const w = mount(TimelinePane, {
      props: {
        events: [
          { seq: 1, kind: 'iter_started', payload: { seq: 1, phase: null } },
        ],
      },
    })
    // No scroll event yet → defaults to pinned (matches a fresh
    // run where the user has not scrolled at all).
    expect(w.find('[data-testid="jump-to-latest"]').exists()).toBe(false)
  })

  it('virtualizes >1000 events: DOM windowed, exposed count == total', () => {
    const many: StreamEvent[] = Array.from({ length: 1500 }, (_, i) => ({
      seq: i + 1,
      kind: 'assistant_text',
      payload: { text: `m${i}`, turn_seq: 0, kind: 'text' },
    }))
    const w = mount(TimelinePane, { props: { events: many } })
    const root = w.find('.timeline')
    // Exposed total = full count (the live-tail parity observable).
    expect(root.attributes('data-event-count')).toBe('1500')
    expect(root.attributes('data-virtualized')).toBe('true')
    // DOM is windowed: far fewer <li> than 1500.
    const renderedRows = w.findAll('li.timeline__row').length
    expect(renderedRows).toBeLessThan(1500)
    expect(Number(root.attributes('data-rendered-rows'))).toBe(renderedRows)
  })

  it('below the virtualization threshold every row renders', () => {
    const w = mount(TimelinePane, { props: { events: MIXED } })
    const root = w.find('.timeline')
    expect(root.attributes('data-virtualized')).toBe('false')
    // 9 events, tool start+end merge ⇒ 8 rows.
    expect(w.findAll('li.timeline__row').length).toBe(8)
    expect(root.attributes('data-event-count')).toBe('8')
  })

  // ── W5 iter filter ──────────────────────────────────────────────────
  // Events carry no iter id; membership is derived from the
  // iter_started/iter_ended boundaries (payload.seq is the iter seq).
  const TWO_ITERS: StreamEvent[] = [
    { seq: 1, kind: 'run_started', payload: { project_id: 1 } },
    { seq: 2, kind: 'iter_started', payload: { seq: 1, phase: 'evaluate' } },
    { seq: 3, kind: 'assistant_text', payload: { text: 'iter-one msg' } },
    {
      seq: 4,
      kind: 'tool_use_start',
      payload: { tool_id: 'a', name: 'Bash', args: { command: 'i1' } },
    },
    { seq: 5, kind: 'iter_ended', payload: { seq: 1, exit_reason: 'signal' } },
    { seq: 6, kind: 'iter_started', payload: { seq: 2, phase: 'plan' } },
    { seq: 7, kind: 'assistant_text', payload: { text: 'iter-two msg' } },
    {
      seq: 8,
      kind: 'signal_emit',
      payload: { kind: 'phase_start', args: { phase: 'plan' } },
    },
    { seq: 9, kind: 'iter_ended', payload: { seq: 2, exit_reason: 'signal' } },
    { seq: 10, kind: 'run_ended', payload: { status: 'done' } },
  ]

  it('with selectedIterSeq set, renders only that iter\'s events', () => {
    const w = mount(TimelinePane, {
      props: { events: TWO_ITERS, selectedIterSeq: 1 },
    })
    const t = w.text()
    expect(t).toContain('iter-one msg')
    expect(t).not.toContain('iter-two msg')
    // iter 1's own boundaries are kept; iter 2's signal is excluded.
    expect(w.find('[data-kind="iter_started"]').exists()).toBe(true)
    expect(w.find('[data-testid="signal-card"]').exists()).toBe(false)
    // run_started (before any iter) and run_ended (after) are not in iter 1.
    expect(w.find('[data-kind="run_started"]').exists()).toBe(false)
    expect(w.find('[data-kind="run_ended"]').exists()).toBe(false)
  })

  it('selecting the other iter swaps the visible events', () => {
    const w = mount(TimelinePane, {
      props: { events: TWO_ITERS, selectedIterSeq: 2 },
    })
    const t = w.text()
    expect(t).toContain('iter-two msg')
    expect(t).not.toContain('iter-one msg')
    expect(w.find('[data-testid="signal-card"]').exists()).toBe(true)
  })

  it('cleared filter (null) shows all events again', () => {
    const w = mount(TimelinePane, {
      props: { events: TWO_ITERS, selectedIterSeq: null },
    })
    const t = w.text()
    expect(t).toContain('iter-one msg')
    expect(t).toContain('iter-two msg')
    expect(w.find('[data-kind="run_started"]').exists()).toBe(true)
    expect(w.find('[data-kind="run_ended"]').exists()).toBe(true)
  })
})
