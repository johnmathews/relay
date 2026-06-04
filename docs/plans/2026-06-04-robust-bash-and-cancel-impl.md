# Robust bash + cancel arc — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make relay's cancel finalisation correct and make the
dashboard tell the truth about in-flight pi activity, by fixing the
process-tree containment bug surfaced by run `20260604-201957-62d5`.

**Architecture:** Pi spawns with `start_new_session=True` so it owns
its own process group (Layer 1). `PiSession.cancel` sends `killpg`
to that group, not just to pi, so descendants — orphans holding pi's
stdout fd open — are reaped together (Layer 2). With the orphan-fd
bug closed, `_drive_iter` unwinds correctly on cancel and the run
row finalises in-process. UI: `RunHealthBadge` classifies on
`lastEventTs` (not heartbeat receivedAt) and adds a `disconnected`
state; `ToolCallCard` renders a ticking "running Ns" chip while a
tool is pending.

**Tech Stack:** Python 3.13, asyncio subprocesses, POSIX process
groups, Vue 3 + Vitest, `vue-test-utils`.

**Scope note:** This plan covers Layer 1+2 (relay/harness) and the
two relay-side Layer 5 fragments. **Out of scope:** Layer 3 (pi-fork
bash timeout), Layer 4 (pi-fork `bash_background` tools), Layer 5
skill text + preamble reminder — the last two depend on Layer 4
landing first. Each is a follow-up plan referencing
`docs/plans/2026-06-04-robust-bash-and-cancel.md` (the design).

---

## File map

**Create:**
- `tests/harness/_fixtures/orphan_holder.sh` — fake-pi shell script
  that forks a backgrounded sleeper and prints its pid as the first
  JSONL line, then `exec sleep 100`. Lets the test verify killpg
  reaches descendants. Committed with `chmod +x`.
- `tests/harness/test_pi_session_cancel_kills_descendants.py` — one
  sync test that drives `PiHarness.spawn` + `PiSession.cancel`
  against the fixture and asserts the sleeper PID is gone.

**Modify:**
- `src/relay/harness/pi.py` — two surgical edits:
  - `PiHarness.spawn` line 474: add `start_new_session=True` to
    `asyncio.create_subprocess_exec`.
  - `PiSession.cancel` (lines 347-356): replace body with
    `os.killpg(self._proc.pid, SIGTERM)` + 5s wait + escalate to
    `SIGKILL` on timeout. Add `os`/`signal`/`contextlib` imports.
- `frontend/src/components/runs/RunHealthBadge.vue` — replace the
  `state` classifier (lines 57-63) with an `anchor`-based version
  that prefers `lastEventTs` and adds a `disconnected` arm; add a
  `health-badge--disconnected` style block; extend the
  `aria-label` switch.
- `frontend/src/components/runs/ToolCallCard.vue` — add an optional
  `startedAt: string | number | null` prop, a self-managed
  `nowMs` ticking ref guarded by `result === undefined`, a
  `runningSeconds` computed, and a "running Ns" chip rendered in
  the existing head row (non-embedded) AND inline before the
  args section (embedded). Add a small style block.
- `frontend/src/components/runs/TimelinePane.vue` line 1160 —
  bind `:started-at="row.event.ts"` on the `ToolCallCard`
  invocation (the event store delivers `row.event.ts` as an ISO
  string per `EventStore.append`).
- `frontend/tests/RunHealthBadge.spec.ts` — append two new
  `it(...)` cases for the lastEventTs-based classifier and the
  `disconnected` state.
- `frontend/tests/ToolCallCard.spec.ts` — append two new `it(...)`
  cases for the running chip (pending → renders + ticks; settled
  → absent).

---

## Task 1: Layer 1+2 — process group + cancel cascade

**Files:**
- Create: `tests/harness/_fixtures/orphan_holder.sh`
- Create: `tests/harness/test_pi_session_cancel_kills_descendants.py`
- Modify: `src/relay/harness/pi.py:347-356, 474`

### Steps

- [ ] **Step 1.1: Add the fixture script**

Create `tests/harness/_fixtures/orphan_holder.sh` with the exact
content below:

```sh
#!/bin/sh
# Fake "pi" for tests/harness/test_pi_session_cancel_kills_descendants.
# Forks a backgrounded sleeper, announces its pid as a JSONL line on
# stdout (so the test can verify the killpg cascade reaches it), then
# blocks. Without Layer 1's start_new_session, killing this "pi" alone
# leaves the sleeper running and holding the stdout pipe's write end
# open — the live bug from run 20260604-201957-62d5.
sleep 100 &
SLEEPER_PID=$!
printf '{"type":"session","id":"test","cwd":"/tmp","sleeper_pid":%s}\n' "$SLEEPER_PID"
exec sleep 100
```

Mark it executable:
```bash
chmod +x tests/harness/_fixtures/orphan_holder.sh
```

- [ ] **Step 1.2: Write the failing test**

Create `tests/harness/test_pi_session_cancel_kills_descendants.py`:

```python
"""Layer 1+2 regression: process-group cancel cascade.

Run 20260604-201957-62d5 wedged on `npm run dev`: pi got SIGTERM,
but vite (a descendant) survived in the orphan tree, holding pi's
stdout fd open. `PiSession.events()`'s `async for raw in
self._proc.stdout` never hit EOF, so `_drive_iter`'s finally never
fired, the loop never returned LoopResult("cancelled"), and the run
row stayed 'running' until container restart.

This test reproduces the cascade by spawning a fake pi (a shell
script that forks a backgrounded sleeper, announces its PID, then
blocks) and asserts that PiSession.cancel reaps the sleeper. With
the pre-fix code (no start_new_session + plain self._proc.terminate)
the test fails: the sleeper outlives cancel and the assertion trips.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

from relay.config import Settings
from relay.harness.pi import PiHarness

FIXTURE = Path(__file__).parent / "_fixtures" / "orphan_holder.sh"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_cancel_kills_descendants_in_pi_process_group() -> None:
    settings = Settings(pi_bin=str(FIXTURE))
    harness = PiHarness(settings=settings)

    async def scenario() -> int:
        session = await harness.spawn(
            prompt="ignored",
            cwd=Path("/tmp"),
            env={},
            signal_config=None,  # type: ignore[arg-type]
        )
        # Read the first stdout line to learn the sleeper PID. We go
        # one level below session.events() because the harness mapper
        # would consume "sleeper_pid" silently — we want the raw line.
        assert session._proc.stdout is not None
        raw = await asyncio.wait_for(
            session._proc.stdout.readline(), timeout=2
        )
        announce = json.loads(raw.decode())
        sleeper_pid = int(announce["sleeper_pid"])
        assert _pid_alive(sleeper_pid), "fixture failed to fork sleeper"
        # Layer 1: pi must own its own process group (pgid == pid).
        pi_pid = session._proc.pid
        assert os.getpgid(pi_pid) == pi_pid, (
            "pi was not spawned with start_new_session=True"
        )
        # Layer 2: cancel must reap the descendant via killpg, not just pi.
        await asyncio.wait_for(session.cancel(), timeout=10)
        return sleeper_pid

    sleeper_pid = asyncio.run(scenario())

    # killpg returns synchronously but the kernel may take a few ms
    # to actually deliver SIGKILL. Poll up to 2s.
    deadline = time.time() + 2
    while time.time() < deadline:
        if not _pid_alive(sleeper_pid):
            break
        time.sleep(0.05)
    assert not _pid_alive(sleeper_pid), (
        f"sleeper pid {sleeper_pid} still alive after cancel — "
        f"killpg did not reach the descendant"
    )
```

- [ ] **Step 1.3: Run the test to verify it fails**

```bash
uv run pytest tests/harness/test_pi_session_cancel_kills_descendants.py -v
```

Expected: FAIL. The `os.getpgid(pi_pid) == pi_pid` assertion (or the
final sleeper-alive assertion) trips because the current spawn does
not pass `start_new_session=True`. **Do not proceed past this step
until you see the failure** — a green test here means the fixture
isn't exercising the bug.

- [ ] **Step 1.4: Apply Layer 1 (process group) in `pi.py`**

In `src/relay/harness/pi.py`, modify `PiHarness.spawn` at line ~474.
Replace the `asyncio.create_subprocess_exec` call:

```python
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=full_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self._settings.pi_stdout_limit,
            # Layer 1 (robust-bash-and-cancel design): pi becomes
            # session leader of a new process group with pgid == pid.
            # Descendants (claude-agent-sdk, bash, npm, vite) join
            # this group unless they explicitly setsid; PiSession.cancel
            # killpg's the whole group so a long-lived descendant
            # (npm run dev) cannot survive as an orphan holding pi's
            # stdout fd open. See run 20260604-201957-62d5.
            start_new_session=True,
        )
```

- [ ] **Step 1.5: Apply Layer 2 (cancel cascade) in `pi.py`**

In the same file, the existing imports at the top read
`import asyncio`, `import json`, `import logging`, `import os`,
`import re`, `import time`. Add `import contextlib` and
`import signal` to that block (alphabetical):

```python
import asyncio
import contextlib
import json
import logging
import os
import re
import signal
import time
```

Then replace `PiSession.cancel` (lines ~347-356) with:

```python
    async def cancel(self) -> None:
        self._cancelled = True
        if self._proc.returncode is not None:
            return
        # Layer 2: kill the whole process group, not just pi. Pi is
        # the session leader (Layer 1 / start_new_session), so its
        # pgid equals its pid. Descendants (claude-agent-sdk, bash,
        # npm run dev, vite) joined the group unless they did their
        # own setsid; killpg reaps them together, the stdout pipe
        # closes, and _drive_iter's `async for raw in
        # self._proc.stdout` unwinds. Without this cascade an
        # orphan (e.g. vite from `npm run dev`) holds pi's stdout
        # fd open forever, _drive_iter never returns, and the run
        # row stays 'running' (the bug from
        # run 20260604-201957-62d5).
        pgid = self._proc.pid
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGTERM)
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(pgid, signal.SIGKILL)
            await self._proc.wait()
```

- [ ] **Step 1.6: Run the new test to verify it passes**

```bash
uv run pytest tests/harness/test_pi_session_cancel_kills_descendants.py -v
```

Expected: PASS.

- [ ] **Step 1.7: Run the full harness test suite to catch regressions**

The existing `test_pi_session_lookahead.py` and other harness tests
use a fake `_FakeProc` rather than a real subprocess, so they should
be unaffected. Verify:

```bash
uv run pytest tests/harness/ -v
```

Expected: all green, no skipped (apart from `PI_INTEGRATION=1`-gated
ones).

- [ ] **Step 1.8: Run the full backend gate**

```bash
uv run ruff check . && uv run mypy && uv run pytest
```

Expected: ruff clean, mypy clean, pytest all green. If mypy
complains about `os.killpg` or `signal.SIGTERM` typing, both are
stdlib-typed and should not flag — investigate before adding
ignores.

- [ ] **Step 1.9: Commit**

```bash
git add tests/harness/_fixtures/orphan_holder.sh \
        tests/harness/test_pi_session_cancel_kills_descendants.py \
        src/relay/harness/pi.py
git commit -m "$(cat <<'EOF'
fix(harness): cancel reaps pi's process group, not just pi

PiHarness.spawn now uses start_new_session=True so pi owns its own
pgid; PiSession.cancel uses killpg(SIGTERM) → 5s wait → killpg(SIGKILL)
to reach every descendant. Without this, an orphan that inherited pi's
stdout fd (e.g. vite from `npm run dev`) kept the pipe alive, _drive_iter
never hit EOF, and the run row never finalised on cancel (the bug from
run 20260604-201957-62d5).

Design: docs/plans/2026-06-04-robust-bash-and-cancel.md (Layer 1+2).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: Layer 5a — `RunHealthBadge` classifier fix

**Files:**
- Modify: `frontend/src/components/runs/RunHealthBadge.vue:57-63`
- Modify: `frontend/src/components/runs/RunHealthBadge.vue:190-193`
  (add a `disconnected` style block)
- Modify: `frontend/src/components/runs/RunHealthBadge.vue:119-132`
  (extend aria-label switch)
- Modify: `frontend/tests/RunHealthBadge.spec.ts` (append two cases)

### Steps

- [ ] **Step 2.1: Update the existing `live → slow → stalled` test**

In `frontend/tests/RunHealthBadge.spec.ts`, the existing transition
test at line 63 passes `lastHeartbeat: snap()` (which defaults
`lastEventTs: null`) and advances the clock without re-sending
heartbeats. Under the new classifier this models "single heartbeat,
SSE then died" — i.e. **disconnected** is the semantically correct
classification at the 90s mark. Update the test to reflect that:

```typescript
  it('transitions live → slow → disconnected when heartbeats stop arriving', async () => {
    const w = mount(RunHealthBadge, {
      props: { status: 'running', lastHeartbeat: snap() },
    })
    const stateNow = (): string | undefined =>
      w.get('[data-testid="run-health-badge"]').attributes('data-state')
    expect(stateNow()).toBe('live')

    // 20 seconds later: lastEventTs is null so anchor falls back to
    // receivedAt. age = 20s > SLOW_MS=15s → slow. The 20s heartbeat-gap
    // check is `> 20_000` and 20_000 is NOT greater, so we fall through
    // to the slow/stalled/live arms here.
    vi.setSystemTime(FIXED_NOW + 20_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(stateNow()).toBe('slow')

    // 90 seconds later: heartbeat-gap = 90_000 > 20_000 → disconnected.
    // (Before the classifier rewrite this was `stalled`; the new
    // semantics treat "no heartbeat for >20s" as SSE-dead, distinct
    // from "pi silent while heartbeats flow".)
    vi.setSystemTime(FIXED_NOW + 90_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(stateNow()).toBe('disconnected')
  })
```

The existing aria-label sub-describe at line 119-131 also asserts
`'Live stream stalled, last activity 2 minutes 5 seconds ago'` at
124s elapsed. Under the new semantics that's `disconnected`, but
the aria-label intentionally omits the duration for `disconnected`
(per Step 2.4 below). Update the test:

```typescript
    it('switches to disconnected once heartbeats stop and drops the duration', async () => {
      const w = mount(RunHealthBadge, {
        props: { status: 'running', lastHeartbeat: snap() },
      })
      vi.setSystemTime(FIXED_NOW + 124_000)
      await vi.advanceTimersByTimeAsync(1_000)
      expect(
        w.get('[data-testid="run-health-badge"]').attributes('aria-label'),
      ).toBe('Live stream disconnected')
    })
```

(Replacing the existing `uses minute granularity once over a minute`
test — the minute-granularity branch is still covered by the
`pluralises and switches state at the slow threshold` test for the
slow→stalled transition, see Step 2.1.5 below.)

- [ ] **Step 2.1.5: Add a slow → stalled aria-label test (replaces the lost minute-granularity coverage)**

The replaced test was the only coverage of the minute-granularity
path. Re-add it using a `lastEventTs`-anchored heartbeat so the
classifier hits the `stalled` arm (not `disconnected`):

```typescript
    it('uses minute granularity once over a minute (stalled via lastEventTs)', () => {
      const oldEventIso = new Date(FIXED_NOW - 125_000).toISOString()
      const w = mount(RunHealthBadge, {
        props: {
          status: 'running',
          lastHeartbeat: snap({ lastEventTs: oldEventIso }),
        },
      })
      expect(
        w.get('[data-testid="run-health-badge"]').attributes('aria-label'),
      ).toBe('Live stream stalled, last activity 2 minutes 5 seconds ago')
    })
```

- [ ] **Step 2.2: Write the new test cases**

Append to `frontend/tests/RunHealthBadge.spec.ts` inside the
existing `describe('RunHealthBadge', ...)` block, after the
`renders a paused run` test (line 94) and before the `aria-label`
sub-describe (line 100):

```typescript
  it('classifies as stalled on a fresh heartbeat with a 75s-old lastEventTs', () => {
    // The live bug: heartbeats kept arriving (5s cadence) so receivedAt
    // stayed fresh, but pi produced no events for 8 minutes. The
    // classifier read receivedAt and reported "live" the whole time.
    // The fix is to anchor state on lastEventTs.
    const oldEventIso = new Date(FIXED_NOW - 75_000).toISOString()
    const w = mount(RunHealthBadge, {
      props: {
        status: 'running',
        lastHeartbeat: snap({ lastEventTs: oldEventIso }),
      },
    })
    const el = w.get('[data-testid="run-health-badge"]')
    expect(el.attributes('data-state')).toBe('stalled')
    expect(el.text().toLowerCase()).toContain('stalled')
  })

  it('classifies as slow on a fresh heartbeat with a 20s-old lastEventTs', () => {
    const oldEventIso = new Date(FIXED_NOW - 20_000).toISOString()
    const w = mount(RunHealthBadge, {
      props: {
        status: 'running',
        lastHeartbeat: snap({ lastEventTs: oldEventIso }),
      },
    })
    const el = w.get('[data-testid="run-health-badge"]')
    expect(el.attributes('data-state')).toBe('slow')
  })

  it('classifies as disconnected when heartbeats themselves stop arriving (>20s)', () => {
    // Distinct from "pi is silent": the SSE connection itself is dead.
    const w = mount(RunHealthBadge, {
      props: {
        status: 'running',
        // receivedAt 25s in the past, lastEventTs fresh: SSE down even
        // though the last persisted event is recent.
        lastHeartbeat: snap({
          receivedAt: FIXED_NOW - 25_000,
          lastEventTs: new Date(FIXED_NOW - 1_000).toISOString(),
        }),
      },
    })
    const el = w.get('[data-testid="run-health-badge"]')
    expect(el.attributes('data-state')).toBe('disconnected')
    expect(el.text().toLowerCase()).toContain('disconnected')
  })
```

- [ ] **Step 2.3: Run the spec to see expected failures**

```bash
cd frontend && npx vitest run RunHealthBadge.spec.ts
```

Expected: FAIL on the three new cases (Step 2.2) and the updated
transition/aria-label tests (Steps 2.1, 2.1.5) because the
classifier still uses the old `receivedAt`-only model and the
`disconnected` state does not exist. **Do not proceed past this
step until you see the failures** — the spec must be exercising
the bug for the fix to be meaningful.

- [ ] **Step 2.4: Update the classifier in `RunHealthBadge.vue`**

In `frontend/src/components/runs/RunHealthBadge.vue`, replace the
`state` computed (lines 57-63) with:

```typescript
// SSE-level liveness: heartbeats fire every 5s. >20s since the last
// arrival means the SSE connection itself is dead — distinct from
// pi being silent. Pi-level liveness: lastEventTs is the wall-clock
// of the most recent persisted event; a silent stream stalls this
// anchor while heartbeats keep flowing.
const HEARTBEAT_GAP_MS = 20_000

const state = computed<
  'connecting' | 'disconnected' | 'live' | 'slow' | 'stalled'
>(() => {
  const hb = props.lastHeartbeat
  if (hb == null) return 'connecting'
  if (nowMs.value - hb.receivedAt > HEARTBEAT_GAP_MS) return 'disconnected'
  const anchor = hb.lastEventTs
    ? Date.parse(hb.lastEventTs)
    : hb.receivedAt
  const age = Math.max(0, nowMs.value - anchor)
  if (age > STALLED_MS) return 'stalled'
  if (age > SLOW_MS) return 'slow'
  return 'live'
})
```

- [ ] **Step 2.5: Extend the `label` and `ariaLabel` switches**

In the same file, add a `disconnected` case to the `label` computed
(lines 82-95):

```typescript
const label = computed((): string => {
  switch (state.value) {
    case 'connecting':
      return 'connecting…'
    case 'disconnected':
      return 'disconnected'
    case 'live':
      return `live · ${sinceLabel.value}`
    case 'slow':
      return `slow · ${sinceLabel.value}`
    case 'stalled':
      return `stalled · ${sinceLabel.value}`
    default:
      return ''
  }
})
```

And to the `ariaLabel` computed (lines 119-132):

```typescript
const ariaLabel = computed((): string => {
  switch (state.value) {
    case 'connecting':
      return 'Live stream connecting'
    case 'disconnected':
      return 'Live stream disconnected'
    case 'live':
      return `Live, last activity ${verboseSinceLabel.value}`
    case 'slow':
      return `Live stream slow, last activity ${verboseSinceLabel.value}`
    case 'stalled':
      return `Live stream stalled, last activity ${verboseSinceLabel.value}`
    default:
      return ''
  }
})
```

- [ ] **Step 2.6: Add a `disconnected` style block**

Insert after the existing `.health-badge--connecting` rule (lines
175-177):

```css
.health-badge--disconnected {
  color: var(--color-text-dim);
  font-style: italic;
}
```

Distinct from `--connecting` (which fires before the first heartbeat,
no italic) — the SSE explicitly died.

- [ ] **Step 2.7: Run the spec to verify it passes**

```bash
cd frontend && npx vitest run RunHealthBadge.spec.ts
```

Expected: PASS, including the existing tests (the
`live → slow → stalled` transition test relied on `lastEventTs:
null`, which falls back to `receivedAt` — unchanged behaviour).

- [ ] **Step 2.8: Run the full frontend gate**

```bash
cd frontend && npm run check
```

Expected: eslint clean (`--max-warnings 0`), `vue-tsc` clean, all
vitests green.

- [ ] **Step 2.9: Commit**

```bash
git add frontend/src/components/runs/RunHealthBadge.vue \
        frontend/tests/RunHealthBadge.spec.ts
git commit -m "$(cat <<'EOF'
fix(frontend): RunHealthBadge anchors on lastEventTs, adds disconnected state

Classifier read heartbeat receivedAt (which advances every 5s
regardless of activity) so a silent pi run rendered as "live" for the
full duration. Anchor on lastEventTs instead, and add a fifth
"disconnected" state for when heartbeats themselves stop arriving
(>20s) — distinct from "pi is silent" (stalled).

Design: docs/plans/2026-06-04-robust-bash-and-cancel.md (Layer 5a).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Layer 5b — `ToolCallCard` "running Ns" badge

**Files:**
- Modify: `frontend/src/components/runs/ToolCallCard.vue` — new
  `startedAt` prop, ticking clock, "running Ns" chip in both
  modes, style block.
- Modify: `frontend/src/components/runs/TimelinePane.vue:1160` —
  pass `:started-at="row.event.ts"`.
- Modify: `frontend/tests/ToolCallCard.spec.ts` — append cases.

### Steps

- [ ] **Step 3.1: Write the failing test cases**

Append to `frontend/tests/ToolCallCard.spec.ts` after the existing
top-level `describe(...)` block:

```typescript
describe('ToolCallCard — running chip', () => {
  const FIXED_NOW = 1_716_000_000_000

  beforeEach(() => {
    vi.useFakeTimers()
    vi.setSystemTime(FIXED_NOW)
  })
  afterEach(() => {
    vi.useRealTimers()
  })

  it('renders "running Ns" while the tool is pending and ticks every second', async () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'sleep 100' },
        startedAt: FIXED_NOW - 3_000,
        // result deliberately omitted → pending
      },
    })
    const chip = w.get('[data-testid="tool-card-running"]')
    expect(chip.text()).toMatch(/running 3s/)

    vi.setSystemTime(FIXED_NOW + 5_000)
    await vi.advanceTimersByTimeAsync(1_000)
    expect(w.get('[data-testid="tool-card-running"]').text())
      .toMatch(/running 8s/)
  })

  it('accepts an ISO string for startedAt and parses it', () => {
    const startedAtIso = new Date(FIXED_NOW - 7_000).toISOString()
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'sleep 100' },
        startedAt: startedAtIso,
      },
    })
    expect(w.get('[data-testid="tool-card-running"]').text())
      .toMatch(/running 7s/)
  })

  it('renders no running chip once a result is present', () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'echo hi' },
        result: 'hi',
        startedAt: FIXED_NOW - 3_000,
        durationMs: 3000,
      },
    })
    expect(w.find('[data-testid="tool-card-running"]').exists()).toBe(false)
  })

  it('renders no running chip when startedAt is null', () => {
    const w = mount(ToolCallCard, {
      props: {
        name: 'Bash',
        args: { command: 'echo hi' },
        startedAt: null,
      },
    })
    expect(w.find('[data-testid="tool-card-running"]').exists()).toBe(false)
  })
})
```

The existing imports `import { describe, it, expect, vi } from
'vitest'` need to add `beforeEach, afterEach`:

```typescript
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
```

- [ ] **Step 3.2: Run the failing test cases**

```bash
cd frontend && npx vitest run ToolCallCard.spec.ts
```

Expected: FAIL — `data-testid="tool-card-running"` does not exist
yet.

- [ ] **Step 3.3: Add the `startedAt` prop + ticking clock to `ToolCallCard.vue`**

In `frontend/src/components/runs/ToolCallCard.vue`, replace the
`import` line (line 13):

```typescript
import { computed, inject, onBeforeUnmount, onMounted, ref } from 'vue'
```

Extend the `defineProps` block (lines 28-46) with `startedAt`:

```typescript
const props = defineProps<{
  /** Tool name (from the `tool_use_start` payload). */
  name: string
  /** Invocation args (from `tool_use_start`). */
  args: unknown
  /** Result (from the paired `tool_use_end`), or undefined if pending. */
  result?: unknown
  /** Whether the tool returned an error (`tool_use_end.is_error`). */
  isError?: boolean
  /** Tool wall-clock in ms (`tool_use_end.duration_ms`). */
  durationMs?: number
  /**
   * When true, render without the outer card border / background / head row.
   * The timeline step-card already supplies the container chrome + name +
   * duration + ok/err glyph in its header; rendering a second outer card
   * inside it produced visible card-in-card nesting.
   */
  embedded?: boolean
  /**
   * `tool_use_start.ts` (ISO string from the SSE envelope, or epoch ms).
   * When set AND `result` is still undefined, the card renders a ticking
   * "running Ns" chip so a long-lived tool call (test runner, npm install)
   * shows visible progress instead of looking frozen. Null/omitted disables
   * the chip — replay rows past their tool_use_end pass null deliberately.
   */
  startedAt?: string | number | null
}>()
```

After the existing `const expanded = ref(false)` (line 68), add the
ticking-clock block:

```typescript
// Ticking-clock for the "running Ns" chip. We only install the
// interval while the tool is pending — once `result` lands the chip
// is gone, so an interval would be busy-work. Mirrors the pattern in
// RunHealthBadge.vue.
const nowMs = ref(Date.now())
let runningTimer: ReturnType<typeof setInterval> | null = null
const isPending = computed(() => props.result === undefined)
const hasStartedAt = computed(() => props.startedAt != null)

onMounted(() => {
  if (!isPending.value || !hasStartedAt.value) return
  runningTimer = setInterval(() => {
    nowMs.value = Date.now()
  }, 1_000)
})
onBeforeUnmount(() => {
  if (runningTimer != null) clearInterval(runningTimer)
})

const runningSeconds = computed((): number | null => {
  if (!isPending.value || props.startedAt == null) return null
  const start = typeof props.startedAt === 'string'
    ? Date.parse(props.startedAt)
    : props.startedAt
  return Math.max(0, Math.floor((nowMs.value - start) / 1_000))
})
```

- [ ] **Step 3.4: Add the "running Ns" chip to the template**

In the same file, insert the running chip into the head row
(between the name span and the error badge — line ~110):

```vue
    <div
      v-if="!embedded"
      class="tool-card__head"
    >
      <span class="tool-card__name">{{ name }}</span>
      <span
        v-if="runningSeconds != null"
        class="tool-card__running"
        data-testid="tool-card-running"
      >running {{ runningSeconds }}s</span>
      <span
        v-if="isError"
        class="tool-card__badge"
      >error</span>
      <span
        v-if="durationMs != null"
        class="tool-card__meta"
      >{{ durationMs }}ms</span>
    </div>
```

For embedded mode (where the step-card supplies the head), add a
small leading chip above the args section so the chip is visible in
both modes. Insert before the `<div class="tool-card__section">`
that wraps args (line ~120):

```vue
    <span
      v-if="embedded && runningSeconds != null"
      class="tool-card__running tool-card__running--embedded"
      data-testid="tool-card-running"
    >running {{ runningSeconds }}s</span>
```

- [ ] **Step 3.5: Add the chip style block**

Insert after the existing `.tool-card__meta` rule (lines 203-207):

```css
.tool-card__running {
  font-size: 0.78em;
  color: var(--color-accent);
  border: 1px solid currentcolor;
  border-radius: 999px;
  padding: 0 0.5em;
  font-variant-numeric: tabular-nums;
}

.tool-card__running--embedded {
  display: inline-block;
  margin-bottom: 0.35rem;
}
```

`tabular-nums` keeps the chip width stable as the digit count
changes (10s → 100s).

- [ ] **Step 3.6: Run the spec to verify it passes**

```bash
cd frontend && npx vitest run ToolCallCard.spec.ts
```

Expected: PASS, including the existing top-level `describe(...)`
block (the new prop is optional and the existing tests do not pass
it).

- [ ] **Step 3.7: Wire `startedAt` in `TimelinePane.vue`**

In `frontend/src/components/runs/TimelinePane.vue` at line ~1160,
add `:started-at="row.event.ts"` to the `ToolCallCard` invocation:

```vue
                    <ToolCallCard
                      v-if="row.type === 'tool'"
                      embedded
                      :name="asStr(row.event.payload.name, 'tool')"
                      :args="row.event.payload.args"
                      :result="row.toolEnd?.result"
                      :is-error="row.toolEnd?.is_error === true"
                      :duration-ms="asNum(row.toolEnd?.duration_ms)"
                      :started-at="row.event.ts"
                    />
```

`row.event` is the `tool_use_start` event; `row.event.ts` is the ISO
timestamp the SSE envelope carries (see `EventStore.append`'s
post-commit publish payload at `events.py:138-143`).

- [ ] **Step 3.8: Run the full frontend gate**

```bash
cd frontend && npm run check
```

Expected: eslint clean, `vue-tsc` clean, all vitests green. If the
TimelinePane test gets noisier ("found running chip"), inspect: the
existing pane tests should not pass `result === undefined` rows
unless they explicitly construct one, so the chip should be absent
in most fixtures. If a fixture trips, fix the fixture by setting
`result` or `startedAt: null`.

- [ ] **Step 3.9: Commit**

```bash
git add frontend/src/components/runs/ToolCallCard.vue \
        frontend/src/components/runs/TimelinePane.vue \
        frontend/tests/ToolCallCard.spec.ts
git commit -m "$(cat <<'EOF'
feat(frontend): ToolCallCard shows a ticking "running Ns" chip while pending

In-flight tool calls were invisible in the timeline between
tool_use_start and tool_use_end: only ephemeral assistant_delta frames
existed, so a 60s test run or a long npm install looked frozen. The
chip uses the same ticking-clock pattern as RunHealthBadge and only
mounts the interval while the call is pending.

Design: docs/plans/2026-06-04-robust-bash-and-cancel.md (Layer 5b).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## End-of-plan verification

After Task 3 commits, run the full project gate one more time from
the repo root:

```bash
uv run ruff check . && uv run mypy && uv run pytest && \
  (cd frontend && npm run check)
```

Expected: every component green. If `uv run pytest` reports the
`PI_INTEGRATION=1`-gated tests as skipped, that is normal — they
require a real pi binary and a Max-subscription auth.

Then do a manual smoke against the deployed agent container after
deploy:

1. `docker compose restart relay` on the agent LXC.
2. Start a small run (any project).
3. Hit **Cancel run** mid-iter.
4. Verify the run row flips to `cancelled` within 10s — no container
   restart needed. (The headline win of this PR.)

---

## Risks

- **`start_new_session=True` on pi**: pi `--mode json` is
  non-interactive, so removing its controlling tty should be a
  no-op. If pi v0.78.0 turns out to read from `/dev/tty` for some
  edge case, the symptom would be a hung iter at startup. Revert and
  investigate in the pi fork.
- **`os.killpg` is Linux/macOS-only**. Relay only runs on POSIX
  hosts; no Windows support is a non-goal (matches existing CI).
- **A descendant that does its own `setsid` escapes the cascade**.
  None of pi's current tools do this; documented in the design as a
  known escape. Workaround if observed: walk
  `/proc/<pid>/task/<tid>/children` recursively.
- **`Settings(pi_bin=…)` and env-var precedence**: pydantic-settings
  may give env vars priority over kwargs; if `RELAY_PI_BIN` is set
  in the test environment, the fixture script will be ignored. The
  test does not currently set/unset it. If CI breaks on this, wrap
  the `Settings(...)` construction in `monkeypatch.delenv(...,
  raising=False)` for `RELAY_PI_BIN`.
