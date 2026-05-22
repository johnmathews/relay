import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UsageRow from '../src/components/runs/UsageRow.vue'

// Real-world pi `SessionEnded.messages[].usage` shape captured from
// `.relay/relay.db` after the 9f acceptance run (ADR-18 — messages are
// opaque, but the documented numeric keys are pi-flavoured: `input`,
// `output`, `cacheRead`, `cacheWrite`, `totalTokens`, plus a `cost`
// dict with `total`). These are the same field names
// `_aggregate_usage` in `observability/otel.py` reads — there is one
// source of truth for which keys to sum, and that is it.
const PI_ASSISTANT_USAGE = {
  input: 3,
  output: 335,
  cacheRead: 0,
  cacheWrite: 19072,
  totalTokens: 19410,
  cost: { input: 9e-6, output: 0.005025, cacheRead: 0, cacheWrite: 0.07152, total: 0.076554 },
}

describe('UsageRow', () => {
  it('sums pi-shape assistant-message usage (input/output/cacheRead/cacheWrite)', () => {
    const wrapper = mount(UsageRow, {
      props: {
        event: {
          seq: 42,
          kind: 'harness_session_ended',
          payload: {
            stop_reason: 'clean',
            summary: 'wrap-up',
            messages: [
              { role: 'user', content: [{ type: 'text', text: 'hi' }] },
              { role: 'assistant', usage: PI_ASSISTANT_USAGE },
            ],
          },
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('clean')
    expect(text).toContain('3') // input
    expect(text).toContain('335') // output
    expect(text).toContain('19072') // cache write — the dominant cache number for pi
  })

  it('sums across multiple assistant messages, ignores user messages', () => {
    const wrapper = mount(UsageRow, {
      props: {
        event: {
          seq: 1,
          kind: 'harness_session_ended',
          payload: {
            stop_reason: 'clean',
            summary: null,
            messages: [
              { role: 'user', content: [{ type: 'text', text: 'go' }] },
              { role: 'assistant', usage: { input: 10, output: 20, cacheRead: 5, cacheWrite: 100 } },
              { role: 'assistant', usage: { input: 30, output: 50, cacheRead: 7, cacheWrite: 200 } },
            ],
          },
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('40') // input sum
    expect(text).toContain('70') // output sum
    expect(text).toContain('12') // cacheRead sum
    expect(text).toContain('300') // cacheWrite sum
  })

  it('renders gracefully when messages is empty', () => {
    const wrapper = mount(UsageRow, {
      props: {
        event: {
          seq: 1,
          kind: 'harness_session_ended',
          payload: { stop_reason: 'cancelled', summary: null, messages: [] },
        },
      },
    })
    expect(wrapper.text()).toContain('cancelled')
    expect(wrapper.text()).toContain('0')
  })
})
