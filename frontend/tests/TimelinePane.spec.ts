import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
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

  it('artifact_edited row is a clickable target that selects the artifact (14e)', async () => {
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
    const store = useBrowserUiStore('run:run-abc')
    expect(store.selectedPath).toBe('improvement-plan.md')
  })

  it('create-path artifact_edited row is also clickable (14e)', async () => {
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

  it('tool-call card collapses >8 lines and "show full" expands', async () => {
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
    expect(collapsed.split('\n').length).toBeLessThanOrEqual(8)

    const toggle = w.find('[data-testid="tool-card-toggle"]')
    expect(toggle.exists()).toBe(true)
    await toggle.trigger('click')
    expect(w.find('.tool-card__block').text().split('\n').length).toBeGreaterThan(
      8,
    )
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
