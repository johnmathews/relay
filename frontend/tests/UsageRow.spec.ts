import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import UsageRow from '../src/components/runs/UsageRow.vue'

describe('UsageRow', () => {
  it('renders stop_reason badge + summed token counts', () => {
    const wrapper = mount(UsageRow, {
      props: {
        event: {
          seq: 42,
          kind: 'harness_session_ended',
          payload: {
            stop_reason: 'clean',
            summary: 'wrap-up',
            messages: [
              { role: 'assistant', usage: { input_tokens: 12, output_tokens: 7, cache_read_input_tokens: 3 } },
              { role: 'assistant', usage: { input_tokens: 30, output_tokens: 21, cache_read_input_tokens: 9 } },
            ],
          },
        },
      },
    })
    const text = wrapper.text()
    expect(text).toContain('clean')
    expect(text).toContain('42') // sum input
    expect(text).toContain('28') // sum output
    expect(text).toContain('12') // sum cache-read
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
