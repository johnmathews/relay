// Phase 5 of the run-detail layout proposal.
// The drawer slides in from the right when a ToolCallCard's "View
// full" affordance is clicked. It is teleported to document.body, so
// assertions reach for the DOM directly rather than through the
// wrapper element.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick, defineComponent, h, ref, type Ref } from 'vue'
import ToolCallDetailDrawer, {
  type ToolCallDrawerPayload,
} from '../src/components/runs/ToolCallDetailDrawer.vue'

// Single shared test parent (vue/one-component-per-file rule). The
// `open` and `payload` refs are passed in by the test setup; the
// parent just reactively forwards them to the drawer so we can drive
// open → close transitions.
interface ParentRefs {
  open: Ref<boolean>
  payload: Ref<ToolCallDrawerPayload | null>
}
let parentRefs: ParentRefs | null = null

const DrawerHarness = defineComponent({
  name: 'DrawerHarness',
  setup() {
    return () =>
      parentRefs == null
        ? null
        : h(ToolCallDetailDrawer, {
            open: parentRefs.open.value,
            payload: parentRefs.payload.value,
          })
  },
})

function basePayload(
  over: Partial<ToolCallDrawerPayload> = {},
): ToolCallDrawerPayload {
  return {
    name: 'Bash',
    args: { command: 'ls -la /tmp' },
    result: 'a\nb\nc',
    isError: false,
    durationMs: 42,
    ...over,
  }
}

function findInBody(selector: string): HTMLElement | null {
  return document.body.querySelector<HTMLElement>(selector)
}

describe('ToolCallDetailDrawer — render gate', () => {
  beforeEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })
  afterEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })

  it('renders nothing when open=false', () => {
    mount(ToolCallDetailDrawer, {
      props: { open: false, payload: basePayload() },
      attachTo: document.body,
    })
    expect(findInBody('[data-testid="tool-drawer"]')).toBeNull()
    expect(findInBody('[data-testid="tool-drawer-backdrop"]')).toBeNull()
  })

  it('renders nothing when open=true but payload is null', () => {
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: null },
      attachTo: document.body,
    })
    expect(findInBody('[data-testid="tool-drawer"]')).toBeNull()
  })

  it('renders dialog with ARIA attrs when open + payload', async () => {
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    const dialog = findInBody('[data-testid="tool-drawer"]')
    expect(dialog).not.toBeNull()
    expect(dialog!.getAttribute('role')).toBe('dialog')
    expect(dialog!.getAttribute('aria-modal')).toBe('true')
    expect(dialog!.getAttribute('aria-label')).toContain('Bash')
    expect(dialog!.textContent).toContain('42ms')
  })
})

describe('ToolCallDetailDrawer — close paths', () => {
  beforeEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })
  afterEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })

  it('emits close on close-button click', async () => {
    const w = mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    const closeBtn = findInBody('[data-testid="tool-drawer-close"]')
    expect(closeBtn).not.toBeNull()
    closeBtn!.click()
    expect(w.emitted('close')).toBeTruthy()
  })

  it('emits close on backdrop click', async () => {
    const w = mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    const backdrop = findInBody('[data-testid="tool-drawer-backdrop"]')
    expect(backdrop).not.toBeNull()
    backdrop!.click()
    expect(w.emitted('close')).toBeTruthy()
  })

  it('does NOT emit close on dialog body click (event stopped)', async () => {
    const w = mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    const dialog = findInBody('[data-testid="tool-drawer"]')!
    dialog.click()
    expect(w.emitted('close')).toBeFalsy()
  })

  it('emits close on Escape keydown inside the dialog', async () => {
    const w = mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    // Focus-trap activate runs in a queueMicrotask after open flips
    // true; flush so the keydown listener is attached.
    await Promise.resolve()
    const dialog = findInBody('[data-testid="tool-drawer"]')!
    dialog.dispatchEvent(
      new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }),
    )
    expect(w.emitted('close')).toBeTruthy()
  })
})

describe('ToolCallDetailDrawer — render-mode dropdown', () => {
  beforeEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })
  afterEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })

  it('defaults to Code mode and shows args + result sections', async () => {
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    const select = findInBody(
      '[data-testid="tool-drawer-mode"]',
    ) as HTMLSelectElement | null
    expect(select).not.toBeNull()
    expect(select!.value).toBe('code')
    expect(findInBody('[data-testid="tool-drawer"]')!.textContent).toContain('args')
    expect(findInBody('[data-testid="tool-drawer"]')!.textContent).toContain('result')
  })

  it('switching to Diff with no old/new shows the empty state', async () => {
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    const select = findInBody(
      '[data-testid="tool-drawer-mode"]',
    ) as HTMLSelectElement
    select.value = 'diff'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(
      findInBody('[data-testid="tool-drawer-diff-empty"]'),
    ).not.toBeNull()
  })

  it('Diff mode renders DiffRender when args carry old/new strings', async () => {
    const payload = basePayload({
      name: 'Edit',
      args: {
        file_path: '/tmp/foo.txt',
        old_string: 'alpha\nbeta',
        new_string: 'alpha\nBETA',
      },
      result: 'File updated',
    })
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload },
      attachTo: document.body,
    })
    await nextTick()
    const select = findInBody(
      '[data-testid="tool-drawer-mode"]',
    ) as HTMLSelectElement
    select.value = 'diff'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(
      findInBody('[data-testid="tool-drawer-diff-empty"]'),
    ).toBeNull()
    // DiffRender uses diff2html which dynamic-imports — wait for its
    // output to land.
    await vi.waitFor(() => {
      const dialog = findInBody('[data-testid="tool-drawer"]')
      expect(dialog?.innerHTML).toMatch(/d2h-/)
    })
  })

  it('resets to Code mode each time the drawer re-opens', async () => {
    const open = ref(true)
    const payload = ref<ToolCallDrawerPayload | null>(basePayload())
    parentRefs = { open, payload }
    mount(DrawerHarness, { attachTo: document.body })
    await nextTick()
    let select = findInBody(
      '[data-testid="tool-drawer-mode"]',
    ) as HTMLSelectElement
    select.value = 'markdown'
    select.dispatchEvent(new Event('change'))
    await nextTick()
    expect(select.value).toBe('markdown')

    open.value = false
    await nextTick()
    open.value = true
    await nextTick()
    select = findInBody(
      '[data-testid="tool-drawer-mode"]',
    ) as HTMLSelectElement
    expect(select.value).toBe('code')
  })
})

describe('ToolCallDetailDrawer — focus trap', () => {
  beforeEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })
  afterEach(() => {
    while (document.body.firstChild) {
      document.body.removeChild(document.body.firstChild)
    }
  })

  it('moves focus into the drawer when opened', async () => {
    // Seed a focusable trigger outside the drawer and focus it; the
    // trap should move focus into the drawer on activate.
    const trigger = document.createElement('button')
    trigger.textContent = 'trigger'
    trigger.setAttribute('data-testid', 'pre-trigger')
    document.body.appendChild(trigger)
    trigger.focus()
    expect(document.activeElement).toBe(trigger)

    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    // Focus activation is queued via queueMicrotask — flush.
    await Promise.resolve()
    await Promise.resolve()
    expect(document.activeElement).not.toBe(trigger)
    const dialog = findInBody('[data-testid="tool-drawer"]')!
    expect(dialog.contains(document.activeElement)).toBe(true)
  })

  it('restores focus to the previously-focused element on close', async () => {
    const trigger = document.createElement('button')
    trigger.textContent = 'trigger'
    document.body.appendChild(trigger)
    trigger.focus()

    const open = ref(true)
    const payload = ref<ToolCallDrawerPayload | null>(basePayload())
    parentRefs = { open, payload }
    mount(DrawerHarness, { attachTo: document.body })
    await nextTick()
    await Promise.resolve()
    await Promise.resolve()

    open.value = false
    await nextTick()
    expect(document.activeElement).toBe(trigger)
  })

  it('Tab wraps from last focusable back to first', async () => {
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    await Promise.resolve()
    await Promise.resolve()
    const dialog = findInBody('[data-testid="tool-drawer"]')!
    // The close button is the last focusable; focus it and Tab → first.
    const closeBtn = findInBody(
      '[data-testid="tool-drawer-close"]',
    ) as HTMLElement
    closeBtn.focus()
    expect(document.activeElement).toBe(closeBtn)
    const ev = new KeyboardEvent('keydown', {
      key: 'Tab',
      bubbles: true,
      cancelable: true,
    })
    dialog.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(true)
    // First focusable is the mode select.
    const select = findInBody('[data-testid="tool-drawer-mode"]')
    expect(document.activeElement).toBe(select)
  })

  it('Shift+Tab wraps from first focusable back to last', async () => {
    mount(ToolCallDetailDrawer, {
      props: { open: true, payload: basePayload() },
      attachTo: document.body,
    })
    await nextTick()
    await Promise.resolve()
    await Promise.resolve()
    const dialog = findInBody('[data-testid="tool-drawer"]')!
    const select = findInBody(
      '[data-testid="tool-drawer-mode"]',
    ) as HTMLElement
    select.focus()
    const ev = new KeyboardEvent('keydown', {
      key: 'Tab',
      shiftKey: true,
      bubbles: true,
      cancelable: true,
    })
    dialog.dispatchEvent(ev)
    expect(ev.defaultPrevented).toBe(true)
    const closeBtn = findInBody('[data-testid="tool-drawer-close"]')
    expect(document.activeElement).toBe(closeBtn)
  })
})
