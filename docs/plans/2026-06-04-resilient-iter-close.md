# Resilient Iter Close — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eliminate the "agent finished a turn cleanly but emitted no terminal sentinel → run finalised as `failed`" cliff. Such runs should self-recover when possible, fall back to a paused state when not, and recoverable legacy `failed` runs should be salvageable from the dashboard.

**Architecture:** Layer five defences along the existing iter-close path in `src/relay/orchestrator/loop.py`. (B) tightens the preamble; (C) tightens the engineering-team skill template. (D) detects `clean stop_reason + no terminal signal` mid-loop and issues exactly one corrective recovery iter (counted in `LoopState`, not `effective_max`) asking pi to re-emit a closing sentinel. (A) is the fallback: if the recovery iter also no-signals, transition the run to `paused` with a synthesised `pause_requested` event (mirroring the chat-mode auto-pause shape already in `loop.py:460-481`) instead of `failed`. (E) adds a dashboard "reopen as paused" affordance for legacy `failed` runs whose last iter's `exit_reason == "agent_end_no_signal"`. A new ADR records the no-signal handling policy; `spec.md` §6 is updated accordingly.

**Tech Stack:** Python 3.13 (`uv`, `pytest`, `ruff`, `mypy --strict`), FastAPI + SQLAlchemy async, Vue 3 + Pinia Colada + openapi-fetch. Scripted harness doubles in `tests/orchestrator/` drive the loop without spawning pi.

---

## File Structure

**Modify:**
- `src/relay/orchestrator/loop.py` — D + A: detect clean+no-signal mid-loop, inject a recovery body and continue; if it recurs, return `LoopResult("paused", reason="agent_end_no_signal_autopause", …)` instead of `failed`.
- `src/relay/orchestrator/preamble.py` — B: append a one-line sentinel reminder in task mode.
- `src/relay/core.py` — E: new `reopen_failed_as_paused(run_id)` method.
- `src/relay/api/runs.py` — E: new `POST /api/runs/{id}/reopen` route.
- `skills/engineering-team/pi/phases/phase-3-development.md` — C: explicit rule for proposal-then-approval handoffs.
- `docs/decisions.md` — append ADR-53 ("Resilient iter close: recovery iter + auto-pause fallback for clean+no-signal").
- `docs/spec.md` §6 — describe the new close paths.
- `frontend/src/components/runs/layout/RunRightPane.vue` — E: add "Reopen as paused" button for the failed+no-signal case (existing `failureInfo.hint` block already exists).
- `frontend/src/api/sse.ts` (KNOWN_EVENT_TYPES) and `frontend/src/stores/events.ts` (INVALIDATING_KINDS) — extend for any new event kinds (see WU5 — none needed if we reuse `pause_requested`).

**Create:**
- `tests/orchestrator/test_recovery_iter.py` — D + A regression coverage.
- `tests/api/test_reopen_run.py` — E backend route coverage.
- `frontend/src/components/runs/layout/ReopenButton.spec.ts` — E UI coverage.

**Read-only references (do not modify):**
- `src/relay/orchestrator/loop.py:433-481` — the chat-mode auto-pause is the structural blueprint for WU4 (synth `signal_args`, `LoopResult("paused", reason=…, pause_id=…)`).
- `tests/orchestrator/test_loop.py:367-396` — `test_fenced_sentinel_no_real_signal_fails_cleanly` is the regression we are about to *change* — it currently asserts `status == "failed"`. WU4 must update it (or this entire failure-mode no longer fails the way the test expects).
- `tests/orchestrator/conftest.py` — `ScriptedHarness`, `TextScript`, `_settings`, `_run`, `_read` helpers (use these, do not roll new fixtures).

---

## Naming Conventions (lock these in early — used across WUs)

- **Recovery-iter trigger condition:** `outcome.signal is None AND outcome.stop_reason == "clean" AND outcome.marker_headline is None AND state.recovery_used is False`. A marker-contract violation (headline set) stays a `failed` run — pi *did* try to emit a sentinel and got it wrong; that is a real bug we want surfaced, not papered over.
- **Loop-local state field:** `recovery_used: bool = False` on a new `_LoopState` dataclass at the top of `loop.py` (or threaded as a local — see WU3 Step 3). The recovery iter does NOT consume `effective_max`; it is a +1 budget extension scoped to one shot.
- **Recovery body text** (verbatim — used in WU3 implementation and WU3 test assertion):
  ```
  RELAY_RECOVERY_NOTICE: Your previous turn ended without a terminal
  sentinel. Relay needs exactly one of `[[engteam:done]]`,
  `[[engteam:handoff]]`, `[[engteam:pause-for-input id="..." question="..."]]`,
  or `[[engteam:fanout]]` at column 0 to close the iter.

  Re-emit your final state. If you were waiting on operator approval,
  the correct closing sentinel is `pause-for-input` — bracket the
  question with `[[engteam:prompt-start]]` / `[[engteam:prompt-end]]`
  per the engineering-team skill's pause protocol.
  ```
- **New `LoopResult.reason` value (A fallback):** `"agent_end_no_signal_autopause"`. Distinct from `"agent_end_no_signal"` (which stays for the marker-violation `failed` path) so the dashboard, OTel attribute, and tests can discriminate.
- **Synth pause id for A:** `f"autopause-{ctx.run_id}-{seq}"` (mirrors chat-mode's `f"chat-{ctx.run_id}-{seq}"` at `loop.py:460`).
- **Synth pause question for A:** `"Agent ended without a terminal sentinel; relay auto-paused. Provide guidance to resume, or close the run."` (one line — surfaces in the dashboard `PauseAnswerForm`).
- **New event payload field on `iter_ended` for the recovery iter:** `recovery_iter: true`. Discriminates in the timeline ("this iter was the corrective retry"). Default-absent for normal iters.
- **E (reopen) route:** `POST /api/runs/{run_id}/reopen` → `RunOut`. 404 if unknown; 409 if not `failed`; 409 if last iter's `exit_reason != "agent_end_no_signal"` AND `!= "agent_end_no_signal_autopause"`. Successful reopen flips `status: failed → paused`, clears `ended_at`, appends a `pause_requested` event with `question` carrying the recovery prompt text. No new event kind needed.

---

## WU1 — Engineering-team skill template tightening (C)

**Files:**
- Modify: `skills/engineering-team/pi/phases/phase-3-development.md`

This work unit is doc-only — no Python tests. Verification is by re-reading the template and confirming the new rule is present.

- [ ] **Step 1: Read the current template's "Pausing for user input" section**

```bash
sed -n '60,95p' skills/engineering-team/pi/phases/phase-3-development.md
```

Expected: existing block titled "Pausing for user input" followed by the `[[engteam:pause-for-input ...]]` template.

- [ ] **Step 2: Insert a new rule above "Pausing for user input"**

Edit `skills/engineering-team/pi/phases/phase-3-development.md`. Find the line `## Pausing for user input` and insert ABOVE it (preserving the existing block intact):

```markdown
## Closing sentinel is mandatory — proposal text alone is not a pause

Every iter MUST end with a terminal sentinel at column 0: `done`,
`handoff`, `pause-for-input`, or `fanout`. Conversational "OK to
proceed?" / "Awaiting your sign-off" / "Let me know if..." text in
the iter's final assistant turn is NOT a substitute. If you have
introduced a new work-unit proposal (e.g. "Here is the shape for
WU0.1 — flag anything to amend") inside the same iter as a
`unit-done`, you are implicitly asking for operator approval and you
MUST close the iter with `pause-for-input`. Failing to do so causes
relay to spend a budgeted recovery iter; failing again causes relay
to auto-pause the run for safety. Both paths are surfaced in the
dashboard as a soft failure — emit the sentinel the first time.

```

(Note the trailing blank line — preserves the existing `## Pausing
for user input` header spacing.)

- [ ] **Step 3: Insert a self-check item in the "Sentinel cadence" recap**

Find the block starting `**Sentinel cadence (recap` near line 275 and edit it to add one more bullet at the end of the existing prose:

```markdown
**Sentinel cadence (recap — see `../references/sentinels.md` for the full rules).**
For every work unit you touch in this phase: emit `[[engteam:unit-start id="W<n>" title="..."]]`
before the first edit, then `[[engteam:unit-done id="W<n>"
title="..."]]` after your review confirms the unit is green, or `[[engteam:unit-abandoned
id="W<n>" reason="..."]]` if you give up on it. Never leave a `unit-start` open at the end
of a session. **And: if your closing assistant turn proposes a new unit and asks
for operator approval, the iter MUST end with `pause-for-input`, not freeform
prose — see "Closing sentinel is mandatory" above.**
```

- [ ] **Step 4: Manually re-read the file end-to-end**

```bash
cat skills/engineering-team/pi/phases/phase-3-development.md | head -100
```

Expected: the new "Closing sentinel is mandatory" section sits between the previous "Sentinel contract (read first)" block and "Pausing for user input"; the Sentinel cadence recap has the new bullet.

- [ ] **Step 5: Commit**

```bash
git add skills/engineering-team/pi/phases/phase-3-development.md
git commit -m "skills(engteam): require pause-for-input when a unit-done iter proposes new work

Phase-3 template now explicitly rules that conversational 'OK to proceed?'
text is not a substitute for a closing sentinel. Paired with the relay-side
recovery-iter + auto-pause fallback landing in this arc (WU3/WU4)."
```

---

## WU2 — Preamble sentinel reminder (B)

**Files:**
- Modify: `src/relay/orchestrator/preamble.py`
- Modify: `tests/orchestrator/test_loop.py` (only if a preamble assertion exists; otherwise add a focused unit test next to `preamble.py`)
- Create: `tests/orchestrator/test_preamble.py` (if no existing test module)

- [ ] **Step 1: Check whether a preamble unit test already exists**

```bash
grep -rn "build_preamble\|compose_prompt" tests/ | head
```

If `tests/orchestrator/test_preamble.py` does NOT exist, create it in Step 2. If it does, jump straight to adding a case for the new line.

- [ ] **Step 2: Write the failing test**

Create `tests/orchestrator/test_preamble.py`:

```python
"""Preamble unit tests (spec §12).

`build_preamble` is the single source of the RELAY_* block. WU2 adds a
sentinel-discipline reminder line; this test pins both the existing
shape and the new line.
"""

from __future__ import annotations

from pathlib import Path

from relay.orchestrator.preamble import build_preamble


def test_preamble_contains_run_dir_and_sentinel_reminder(tmp_path: Path) -> None:
    out = build_preamble(tmp_path / ".relay" / "runs" / "r1", phase=None)
    lines = out.splitlines()
    assert lines[0].startswith("RELAY_RUN_DIR:")
    # WU2: every iter's preamble reminds the agent that a closing
    # sentinel is mandatory. Defends the no-signal cliff with a cheap
    # pre-emptive nudge (paired with WU3 recovery + WU4 auto-pause).
    assert any(
        "closing sentinel" in line.lower() for line in lines
    ), f"expected a sentinel-discipline reminder line; got {out!r}"


def test_preamble_omits_phase_when_absent(tmp_path: Path) -> None:
    out = build_preamble(tmp_path, phase=None)
    assert "RELAY_PHASE" not in out


def test_preamble_includes_phase_when_present(tmp_path: Path) -> None:
    out = build_preamble(tmp_path, phase="development")
    assert "RELAY_PHASE: development" in out
```

- [ ] **Step 3: Run the test to verify it fails**

```bash
uv run pytest tests/orchestrator/test_preamble.py -v
```

Expected: `test_preamble_contains_run_dir_and_sentinel_reminder` FAILS with "expected a sentinel-discipline reminder line"; the other two PASS.

- [ ] **Step 4: Modify `build_preamble` to emit the reminder**

Edit `src/relay/orchestrator/preamble.py`. Change the body of `build_preamble`:

```python
def build_preamble(run_dir: Path, phase: str | None) -> str:
    """Render the preamble block (no trailing separator).

    ``phase`` is ``None`` until the first ``phase-start`` is observed;
    the ``RELAY_PHASE`` line is then omitted entirely rather than emitted
    empty, so the skill's "no RELAY_PHASE → infer from disk" path triggers
    cleanly.

    The trailing ``RELAY_SENTINEL_REMINDER`` line (WU2 of the resilient-
    iter-close arc) nudges the agent toward emitting a closing sentinel
    every turn — defends the loop's terminal-signal contract pre-emptively
    so the recovery-iter path (WU3) and auto-pause fallback (WU4) are
    second and third lines of defence, not first.
    """
    lines = [f"RELAY_RUN_DIR: {run_dir}"]
    if phase:
        lines.append(f"RELAY_PHASE: {phase}")
    lines.append(
        "RELAY_SENTINEL_REMINDER: end every turn with a closing sentinel at "
        "column 0 — done / handoff / pause-for-input / fanout."
    )
    return "\n".join(lines)
```

- [ ] **Step 5: Re-run the test, then the full backend gate**

```bash
uv run pytest tests/orchestrator/test_preamble.py -v
uv run pytest
uv run ruff check .
uv run mypy
```

Expected: preamble tests all PASS; full backend test run PASSES (other tests that pin the literal preamble text will need adjustment — search and patch).

If a different test asserts a literal preamble byte-pattern (`grep -rn 'RELAY_RUN_DIR:' tests/`), update it to allow the new line; do not delete those assertions.

- [ ] **Step 6: Commit**

```bash
git add src/relay/orchestrator/preamble.py tests/orchestrator/test_preamble.py
git commit -m "preamble: add RELAY_SENTINEL_REMINDER line (WU2 — resilient-iter-close)

Every task-mode iter's preamble now carries an explicit reminder that a
closing sentinel is mandatory. Cheap pre-emptive nudge; paired with the
recovery-iter (WU3) and auto-pause fallback (WU4) landing in this arc."
```

---

## WU3 — Recovery iter for clean+no-signal (D)

**Files:**
- Modify: `src/relay/orchestrator/loop.py`
- Create: `tests/orchestrator/test_recovery_iter.py`

This is the heart of the arc. The loop currently treats `outcome.signal is None AND outcome.stop_reason == "clean"` as `agent_end_no_signal` and returns `LoopResult("failed", …)` (`loop.py:483-514`). We replace that with: if no recovery has been tried yet AND no marker-contract violation, inject a recovery body and `continue` the `while seq < effective_max` loop with `effective_max` widened by 1 for the recovery shot.

- [ ] **Step 1: Write the failing tests**

Create `tests/orchestrator/test_recovery_iter.py`:

```python
"""Recovery-iter regression coverage (WU3 — resilient-iter-close arc).

When a task-mode iter ends with a clean stop_reason and no terminal
sentinel, the loop should issue exactly ONE corrective recovery iter
carrying a RELAY_RECOVERY_NOTICE body asking the agent to re-emit a
closing sentinel. If the recovery iter itself produces a clean
terminal signal (e.g. ``done``), the run finalises normally. If the
recovery iter also ends with no signal, WU4's auto-pause kicks in
(covered in test_autopause_fallback.py).

A marker-contract violation (handoff with no prompt-start/prompt-end)
is NOT recoverable — it is a real bug, not a missing-sentinel
omission. That path still returns ``failed``.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from relay.core import RelayCore
from relay.db.models import Event, Iter, Run

# Reuse the existing test harness scaffolding from test_loop.py.
from tests.orchestrator.test_loop import (
    DONE,
    FENCED_NO_SIGNAL,
    HANDOFF_NO_MARKERS,
    ScriptedHarness,
    TextScript,
    _read,
    _run,
    _settings,
)


def test_clean_no_signal_triggers_recovery_iter(tmp_path: Path) -> None:
    """Iter 1 ends clean with no sentinel → recovery iter (iter 2) carries
    a RELAY_RECOVERY_NOTICE prompt; if iter 2 emits ``done``, the run
    finalises as ``done``."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(FENCED_NO_SIGNAL), TextScript(DONE)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "done"
        iters = list(
            s.scalars(
                select(Iter).where(Iter.run_id == run_id).order_by(Iter.seq)
            )
        )
        assert [it.seq for it in iters] == [1, 2]
        # Iter 1: no signal, exit_reason classifies as no-signal but the
        # loop did not finalise failed.
        assert iters[0].signal_kind is None
        assert iters[0].exit_reason == "agent_end_no_signal"
        # Iter 2: the recovery iter, prompt carries the marker.
        assert "RELAY_RECOVERY_NOTICE" in iters[1].prompt
        assert iters[1].signal_kind == "done"
        # The recovery iter's `iter_ended` event carries `recovery_iter: true`.
        ended_evs = list(
            s.scalars(
                select(Event)
                .where(Event.run_id == run_id, Event.kind == "iter_ended")
                .order_by(Event.seq)
            )
        )
        assert ended_evs[1].payload.get("recovery_iter") is True
        # Iter 1's iter_ended does NOT carry the flag.
        assert "recovery_iter" not in ended_evs[0].payload


def test_marker_violation_skips_recovery_iter(tmp_path: Path) -> None:
    """A marker-contract violation (handoff with no prompt markers) is
    NOT a missing-sentinel case — relay should NOT spend a recovery
    iter on it; current ``failed`` behaviour stands."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(HANDOFF_NO_MARKERS)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"
        assert result.reason == "agent_end_no_signal"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        iters = list(s.scalars(select(Iter).where(Iter.run_id == run_id)))
        # Exactly one iter — no recovery iter spawned.
        assert len(iters) == 1


def test_recovery_iter_does_not_consume_max_iters(tmp_path: Path) -> None:
    """A run with max_iters=1 that no-signals on iter 1 should still get
    its recovery iter — the recovery shot is a +1 extension, not a
    consumption of the user-budgeted iter count."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(FENCED_NO_SIGNAL), TextScript(DONE)])

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.", max_iters=1)
        result = await core.wait_for_run(run_id)
        assert result.status == "done"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        iters = list(
            s.scalars(
                select(Iter).where(Iter.run_id == run_id).order_by(Iter.seq)
            )
        )
        # 1 budgeted iter + 1 recovery shot = 2 total. max_iters was 1.
        assert [it.seq for it in iters] == [1, 2]
```

The constants `DONE`, `FENCED_NO_SIGNAL`, `HANDOFF_NO_MARKERS` are defined at the top of `tests/orchestrator/test_loop.py`. Verify before continuing:

```bash
grep -n "^DONE\|^FENCED_NO_SIGNAL\|^HANDOFF_NO_MARKERS" tests/orchestrator/test_loop.py
```

If `DONE` is not a module-level constant, find the equivalent canonical `done`-sentinel script string used elsewhere in that file and use that name. The test file imports must match.

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/orchestrator/test_recovery_iter.py -v
```

Expected:
- `test_clean_no_signal_triggers_recovery_iter` FAILS (run lands `failed`, not `done`; iters list is `[1]` not `[1, 2]`).
- `test_marker_violation_skips_recovery_iter` PASSES (current behaviour).
- `test_recovery_iter_does_not_consume_max_iters` FAILS (run lands `failed`).

- [ ] **Step 3: Implement the recovery-iter branch in `loop.py`**

Edit `src/relay/orchestrator/loop.py`. Two changes inside `run_loop`:

**3a.** Add a one-shot state local at the top of the loop body (immediately after `effective_max = max(ctx.max_iters, seq + 1)`):

```python
    # WU3 (resilient-iter-close): a clean stop_reason with no terminal
    # sentinel gets ONE corrective retry — the recovery iter — before
    # WU4's auto-pause fallback kicks in. ``recovery_used`` ensures the
    # retry is one-shot; a recovery iter that itself no-signals falls
    # through to WU4 in the same ``signal is None`` branch below.
    recovery_used = False
```

**3b.** Inside the existing `if signal is None:` branch (currently at `loop.py:483-514`), replace the body with the recovery-iter logic. Before:

```python
            signal = outcome.signal
            if signal is None:
                # ... existing no-signal handling that returns failed ...
                iter_span.set_exit(reason)
                await _finish_iter(...)
                return LoopResult(
                    "failed", reason=reason,
                    summary=outcome.marker_headline,
                )
```

After:

```python
            signal = outcome.signal
            if signal is None:
                # No usable closing signal. Three sub-cases:
                #   (1) clean stop, no marker headline, recovery unused:
                #       WU3 — issue ONE corrective recovery iter.
                #   (2) clean stop, no marker headline, recovery already
                #       used: WU4 — auto-pause (next branch).
                #   (3) marker-contract violation OR non-clean stop
                #       (crash): existing failed behaviour stands —
                #       pi tried to emit a sentinel and got it wrong
                #       (1) is the only case the operator can rescue
                #       by retrying; (3) is a real bug we surface.
                is_recoverable_no_signal = (
                    outcome.marker_headline is None
                    and outcome.stop_reason == "clean"
                )
                if is_recoverable_no_signal and not recovery_used:
                    recovery_used = True
                    iter_span.set_exit("agent_end_no_signal")
                    await _finish_iter(
                        store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                        signal_kind=None, signal_args=None,
                        exit_reason="agent_end_no_signal",
                        stop_reason=outcome.stop_reason,
                        messages=outcome.messages,
                        recovery_iter=False,
                    )
                    # Widen the budget by 1 so the recovery iter does
                    # NOT consume a user-budgeted slot.
                    effective_max += 1
                    # Inject the recovery body for the next iter; loop
                    # body recomputes `full_prompt = compose_prompt(...)`
                    # at top of the while-loop, so this is the
                    # carry-forward equivalent of the handoff path.
                    body = _RECOVERY_BODY
                    continue
                # Marker violation OR crash OR recovery-already-used.
                # WU4 catches the recovery-already-used path before this
                # block; here we keep the existing failed behaviour for
                # the other two cases.
                if (
                    outcome.marker_headline
                    or outcome.stop_reason == "clean"
                ):
                    reason = "agent_end_no_signal"
                else:
                    reason = outcome.stop_reason  # 'crash'
                args = (
                    {"marker_error": outcome.marker_headline}
                    if outcome.marker_headline
                    else None
                )
                iter_span.set_exit(reason)
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=args,
                    exit_reason=reason,
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                )
                return LoopResult(
                    "failed", reason=reason,
                    summary=outcome.marker_headline,
                )
```

**3c.** Add the recovery body constant at module level, just below the `_TERMINAL` set (around `loop.py:65`):

```python
_RECOVERY_BODY = (
    "RELAY_RECOVERY_NOTICE: Your previous turn ended without a terminal\n"
    "sentinel. Relay needs exactly one of `[[engteam:done]]`,\n"
    "`[[engteam:handoff]]`, `[[engteam:pause-for-input id=\"...\" question=\"...\"]]`,\n"
    "or `[[engteam:fanout]]` at column 0 to close the iter.\n"
    "\n"
    "Re-emit your final state. If you were waiting on operator approval,\n"
    "the correct closing sentinel is `pause-for-input` — bracket the\n"
    "question with `[[engteam:prompt-start]]` / `[[engteam:prompt-end]]`\n"
    "per the engineering-team skill's pause protocol."
)
```

**3d.** Widen `_finish_iter` to accept a `recovery_iter: bool = False` kwarg that flows into the `iter_ended` event payload. Edit `_finish_iter` signature + body:

```python
async def _finish_iter(
    store: EventStore,
    *,
    run_id: str,
    iter_id: int,
    seq: int,
    signal_kind: str | None,
    signal_args: dict[str, Any] | None,
    exit_reason: str,
    stop_reason: str,
    messages: list[Any],
    summary: str | None = None,
    recovery_iter: bool = False,
) -> None:
    # ... existing harness_session_ended append unchanged ...
    await close_iter(
        store.sessionmaker,
        iter_id,
        signal_kind=signal_kind,
        signal_args=signal_args,
        exit_reason=exit_reason,
    )
    iter_ended_payload: dict[str, Any] = {
        "seq": seq, "signal_kind": signal_kind, "exit_reason": exit_reason,
    }
    if recovery_iter:
        iter_ended_payload["recovery_iter"] = True
    await store.append(
        run_id,
        "iter_ended",
        iter_ended_payload,
        iter_id=iter_id,
    )
```

**3e.** Identify the recovery iter at its closing call site. The recovery iter is whichever iter ran with `body == _RECOVERY_BODY`. The cleanest way to track this at iter close: capture a local `is_recovery_iter = (body == _RECOVERY_BODY)` at the top of the loop body, and pass `recovery_iter=is_recovery_iter` into EVERY `_finish_iter` call inside that iteration of the loop. Cancellation/timeout paths set the flag too — the operator can see in the timeline that the recovery shot was cancelled, not the original.

Concretely, at the top of the `while seq < effective_max:` body, immediately before `seq += 1`:

```python
    while seq < effective_max:
        seq += 1
        is_recovery_iter = body == _RECOVERY_BODY
        if is_chat:
            ...
```

Then thread `recovery_iter=is_recovery_iter` through each `_finish_iter(...)` call in the iteration body (cancelled, timed_out, no-signal, signal-closed paths). The recovery-iter branch added in 3b that *triggers* the next iter writes `recovery_iter=False` for the iter that produced the no-signal — only the RECOVERY iter's `iter_ended` carries the flag, not the iter that triggered the recovery.

- [ ] **Step 4: Run the recovery-iter tests to verify they pass**

```bash
uv run pytest tests/orchestrator/test_recovery_iter.py -v
```

Expected: all three tests PASS.

- [ ] **Step 5: Run the full backend gate**

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

Expected: PASSES. `test_fenced_sentinel_no_real_signal_fails_cleanly` in `test_loop.py:367` will FAIL — it asserted the old failed-without-recovery behaviour. WU4 updates that test. To unblock WU3 commit, mark it `@pytest.mark.skip(reason="WU4 will rewrite — runs no longer fail on clean+no-signal")` and add a TODO comment pointing at WU4 Step 2. Do NOT delete it.

- [ ] **Step 6: Commit**

```bash
git add src/relay/orchestrator/loop.py tests/orchestrator/test_recovery_iter.py tests/orchestrator/test_loop.py
git commit -m "loop: corrective recovery iter on clean+no-signal (WU3)

A task-mode iter that ends cleanly with no terminal sentinel now spawns
ONE corrective recovery iter carrying a RELAY_RECOVERY_NOTICE body. The
recovery iter is a +1 budget extension, not a max_iters consumption.
Marker-contract violations still fail (real bug, not omission). WU4
adds the auto-pause fallback when the recovery iter also no-signals;
test_fenced_sentinel_no_real_signal_fails_cleanly skipped pending that
rewrite."
```

---

## WU4 — Auto-pause fallback (A)

**Files:**
- Modify: `src/relay/orchestrator/loop.py`
- Modify: `tests/orchestrator/test_loop.py` (unskip and rewrite `test_fenced_sentinel_no_real_signal_fails_cleanly`)
- Create: `tests/orchestrator/test_autopause_fallback.py`

When the recovery iter ALSO produces a clean stop with no terminal sentinel, instead of returning `LoopResult("failed", …)`, return `LoopResult("paused", reason="agent_end_no_signal_autopause", question=…, pause_id=…)` so `_apply_result` in `core.py` writes `status=paused` and emits `pause_requested`.

- [ ] **Step 1: Write the failing fallback test**

Create `tests/orchestrator/test_autopause_fallback.py`:

```python
"""Auto-pause fallback (WU4 — resilient-iter-close arc).

When the recovery iter itself ends clean with no terminal sentinel,
the run auto-pauses with a synthesised pause_requested instead of
failing. The dashboard then offers the operator a chance to resume
with guidance — matching the dropped-cliff fix's design intent.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select

from relay.core import RelayCore
from relay.db.models import Event, Iter, Run

from tests.orchestrator.test_loop import (
    FENCED_NO_SIGNAL,
    ScriptedHarness,
    TextScript,
    _read,
    _run,
    _settings,
)


def test_recovery_iter_also_no_signal_auto_pauses(tmp_path: Path) -> None:
    """Two consecutive clean+no-signal iters: the run lands paused, not
    failed; a pause_requested event carries the recovery question."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(FENCED_NO_SIGNAL)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "paused"
        assert result.reason == "agent_end_no_signal_autopause"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "paused"
        # Both iters closed; both carry exit_reason agent_end_no_signal.
        iters = list(
            s.scalars(
                select(Iter).where(Iter.run_id == run_id).order_by(Iter.seq)
            )
        )
        assert [it.seq for it in iters] == [1, 2]
        assert all(it.exit_reason == "agent_end_no_signal" for it in iters)
        # The closing event is pause_requested with the autopause question.
        last = s.scalars(
            select(Event).where(Event.run_id == run_id)
            .order_by(Event.seq.desc()).limit(1)
        ).one()
        assert last.kind == "pause_requested"
        assert "auto-paused" in last.payload["question"].lower()
        # No run_ended event — the run is not terminal.
        ended = list(
            s.scalars(
                select(Event).where(
                    Event.run_id == run_id, Event.kind == "run_ended"
                )
            )
        )
        assert ended == []


def test_autopause_iter_carries_synth_signal_args(tmp_path: Path) -> None:
    """The auto-paused (recovery) iter's row stores signal_kind=pause and
    signal_args containing the synth pause_id and question, so dashboard
    timeline rendering does not special-case the autopause variant."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(FENCED_NO_SIGNAL)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        await core.wait_for_run(run_id)
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        recovery_iter = s.scalars(
            select(Iter)
            .where(Iter.run_id == run_id, Iter.seq == 2)
        ).one()
        assert recovery_iter.signal_kind == "pause"
        args = recovery_iter.signal_args or {}
        assert args["id"].startswith(f"autopause-{run_id}-")
        assert "auto-paused" in args["question"].lower()
```

- [ ] **Step 2: Rewrite `test_fenced_sentinel_no_real_signal_fails_cleanly`**

Edit `tests/orchestrator/test_loop.py`. Find the skipped `test_fenced_sentinel_no_real_signal_fails_cleanly`, unskip, and replace its body:

```python
def test_fenced_sentinel_no_real_signal_recovers_or_autopauses(
    tmp_path: Path,
) -> None:
    """A handoff sentinel only inside a fenced/indented block — never at
    column 0 — yields no signal. With the WU3 recovery iter, the run
    gets ONE retry; with WU4, if the retry also no-signals the run
    lands paused, not failed."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FENCED_NO_SIGNAL), TextScript(FENCED_NO_SIGNAL)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "paused"
        assert result.reason == "agent_end_no_signal_autopause"
        return run_id

    run_id = _run(scenario, settings, harness)
    with _read(settings) as s:
        run = s.get(Run, run_id)
        assert run is not None and run.status == "paused"
        iters = list(s.scalars(select(Iter).where(Iter.run_id == run_id)))
        assert len(iters) == 2
        assert all(it.exit_reason == "agent_end_no_signal" for it in iters)
```

- [ ] **Step 3: Run the new tests to verify they fail**

```bash
uv run pytest tests/orchestrator/test_autopause_fallback.py tests/orchestrator/test_loop.py::test_fenced_sentinel_no_real_signal_recovers_or_autopauses -v
```

Expected: all three FAIL — the recovery-already-used branch currently still returns `LoopResult("failed", …)`.

- [ ] **Step 4: Implement the auto-pause branch in `loop.py`**

Edit `src/relay/orchestrator/loop.py`. Inside the `if signal is None:` block (introduced in WU3 Step 3b), insert the auto-pause branch BEFORE the trailing `failed` block:

```python
            signal = outcome.signal
            if signal is None:
                is_recoverable_no_signal = (
                    outcome.marker_headline is None
                    and outcome.stop_reason == "clean"
                )
                if is_recoverable_no_signal and not recovery_used:
                    # ... WU3 recovery-iter branch unchanged ...
                    continue
                if is_recoverable_no_signal and recovery_used:
                    # WU4: recovery iter also produced no terminal
                    # sentinel. Auto-pause instead of failing — the
                    # agent is clearly stuck on something the operator
                    # can unblock. Mirrors the chat-mode synth-pause
                    # shape at `loop.py:460-481`.
                    pause_id = f"autopause-{ctx.run_id}-{seq}"
                    pause_question = (
                        "Agent ended without a terminal sentinel; relay "
                        "auto-paused. Provide guidance to resume, or "
                        "close the run."
                    )
                    synth_args: dict[str, Any] = {
                        "id": pause_id,
                        "question": pause_question,
                        "next_prompt": "",
                        "review_paths": [],
                    }
                    iter_span.set_exit("agent_end_no_signal")
                    await _finish_iter(
                        store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                        signal_kind="pause", signal_args=synth_args,
                        exit_reason="agent_end_no_signal",
                        stop_reason=outcome.stop_reason,
                        messages=outcome.messages,
                        recovery_iter=is_recovery_iter,
                    )
                    return LoopResult(
                        "paused",
                        reason="agent_end_no_signal_autopause",
                        question=pause_question,
                        next_prompt="",
                        pause_id=pause_id,
                    )
                # Marker violation OR crash → failed (unchanged).
                # ... existing failed branch from WU3 below ...
```

The trailing failed branch keeps the same shape — it now only fires on (marker_headline set) OR (stop_reason != "clean"). The clean+no-signal+recovery-used path is intercepted above.

- [ ] **Step 5: Run the new tests**

```bash
uv run pytest tests/orchestrator/test_autopause_fallback.py tests/orchestrator/test_loop.py::test_fenced_sentinel_no_real_signal_recovers_or_autopauses -v
```

Expected: PASS.

- [ ] **Step 6: Run the full backend gate**

```bash
uv run pytest
uv run ruff check .
uv run mypy
```

Expected: PASS. If the existing `test_marker_violation_fails_cleanly` at `test_loop.py:399` fails, the WU3 sub-case sieve is wrong — fix `is_recoverable_no_signal` so marker headlines still fail.

- [ ] **Step 7: Commit**

```bash
git add src/relay/orchestrator/loop.py tests/orchestrator/test_autopause_fallback.py tests/orchestrator/test_loop.py
git commit -m "loop: auto-pause when recovery iter also no-signals (WU4)

When the WU3 recovery iter itself ends cleanly with no terminal sentinel,
the run lands paused (synthesised pause_requested with a recovery question)
instead of failed. The dashboard's existing PauseAnswerForm picks it up
unchanged; reason='agent_end_no_signal_autopause' discriminates from
the operator-emitted pause-for-input variant in telemetry."
```

---

## WU5 — Dashboard "reopen as paused" for legacy failed runs (E)

**Files:**
- Modify: `src/relay/core.py` — new `reopen_failed_as_paused(run_id)` method.
- Modify: `src/relay/api/runs.py` — new `POST /api/runs/{run_id}/reopen` route.
- Create: `tests/api/test_reopen_run.py`.
- Modify: `frontend/src/components/runs/layout/RunRightPane.vue` — add button + handler.
- Regenerate: `frontend/src/api/types.ts` via `npm run gen:api`.

### Backend (E1)

- [ ] **Step 1: Write the failing backend tests**

Create `tests/api/test_reopen_run.py`:

```python
"""Backend route POST /api/runs/{id}/reopen (WU5 — resilient-iter-close).

Reopens a failed+no-signal run as paused so the operator can resume it
with guidance. 404 unknown; 409 not failed; 409 last iter not no-signal.
On success: status flips to paused, ended_at cleared, pause_requested
event appended with a recovery question.
"""

from __future__ import annotations

import httpx
import pytest
from httpx import ASGITransport
from sqlalchemy import select

from relay.app import create_app
from relay.db.models import Event, Run
# Reuse the API conftest harness double / settings / sessionmaker seam.


@pytest.mark.asyncio
async def test_reopen_failed_no_signal_run_lands_paused(
    seeded_failed_no_signal_run,  # fixture: yields (settings, run_id)
) -> None:
    settings, run_id = seeded_failed_no_signal_run
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app(settings=settings)),
        base_url="http://test",
    ) as client, client._transport_app.router.lifespan_context(
        client._transport_app
    ):
        r = await client.post(f"/api/runs/{run_id}/reopen")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "paused"
        assert body["ended_at"] is None

    # Event store: pause_requested appended with recovery question.
    with settings.sync_session() as s:
        last_pause = s.scalars(
            select(Event).where(
                Event.run_id == run_id, Event.kind == "pause_requested"
            ).order_by(Event.seq.desc()).limit(1)
        ).one()
        assert "auto-paused" in last_pause.payload["question"].lower()


@pytest.mark.asyncio
async def test_reopen_unknown_run_404(empty_settings) -> None:
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app(settings=empty_settings)),
        base_url="http://test",
    ) as client, client._transport_app.router.lifespan_context(
        client._transport_app
    ):
        r = await client.post("/api/runs/does-not-exist/reopen")
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_reopen_done_run_409(seeded_done_run) -> None:
    settings, run_id = seeded_done_run
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app(settings=settings)),
        base_url="http://test",
    ) as client, client._transport_app.router.lifespan_context(
        client._transport_app
    ):
        r = await client.post(f"/api/runs/{run_id}/reopen")
        assert r.status_code == 409
        assert "not failed" in r.text.lower() or "status" in r.text.lower()


@pytest.mark.asyncio
async def test_reopen_failed_timeout_run_409(
    seeded_failed_timeout_run,  # last iter exit_reason == "timeout"
) -> None:
    settings, run_id = seeded_failed_timeout_run
    async with httpx.AsyncClient(
        transport=ASGITransport(app=create_app(settings=settings)),
        base_url="http://test",
    ) as client, client._transport_app.router.lifespan_context(
        client._transport_app
    ):
        r = await client.post(f"/api/runs/{run_id}/reopen")
        assert r.status_code == 409
        assert "no_signal" in r.text or "exit_reason" in r.text
```

The fixtures `seeded_failed_no_signal_run`, `seeded_done_run`, `seeded_failed_timeout_run`, and `empty_settings` need to be added to `tests/api/conftest.py` if they do not exist. Each one runs a scripted scenario via `_run(...)` (from `tests/orchestrator/test_loop.py`) to land a run in the desired terminal state, then yields `(settings, run_id)` for the test. The pattern mirrors the existing API conftest seeders (look for `seeded_*_run` style names in `tests/api/conftest.py` first; reuse those if they cover the cases).

- [ ] **Step 2: Run the tests to verify they fail**

```bash
uv run pytest tests/api/test_reopen_run.py -v
```

Expected: all FAIL — `/api/runs/{id}/reopen` returns 404 Not Found because the route does not exist.

- [ ] **Step 3: Implement `RelayCore.reopen_failed_as_paused`**

Edit `src/relay/core.py`. Add a method on `RelayCore` near `cancel_run`:

```python
async def reopen_failed_as_paused(self, run_id: str) -> None:
    """Convert a ``failed`` run whose last iter ended without a terminal
    sentinel back into a ``paused`` run so the operator can resume it
    with guidance.

    Precondition: the run exists, ``status == "failed"``, and the most
    recent iter's ``exit_reason`` is one of
    ``"agent_end_no_signal"`` / ``"agent_end_no_signal_autopause"``.
    Other failure modes (crash, timeout, marker contract violation) are
    not reopen-eligible — they represent real bugs, not omissions.

    On success: ``status`` flips to ``"paused"``, ``ended_at`` cleared,
    one ``pause_requested`` event appended with a recovery question that
    matches the WU4 auto-pause shape.

    Raises ``ValueError("unknown run …")`` if the run does not exist.
    Raises ``ValueError("run … is not failed (status='X')")`` if the run
    is not failed.
    Raises ``ValueError("run …'s last iter has exit_reason 'X'; "
    "only no-signal failures can be reopened")`` if the last iter is
    not a no-signal failure.
    """
    async with self._sm() as s:
        run = await s.get(Run, run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        if run.status != "failed":
            raise ValueError(
                f"run {run_id} is not failed (status={run.status!r})"
            )
        last_iter = await s.scalar(
            select(Iter)
            .where(Iter.run_id == run_id)
            .order_by(Iter.seq.desc())
            .limit(1)
        )
        eligible_reasons = {
            "agent_end_no_signal",
            "agent_end_no_signal_autopause",
        }
        if last_iter is None or last_iter.exit_reason not in eligible_reasons:
            actual = (
                last_iter.exit_reason if last_iter is not None else "(no iter)"
            )
            raise ValueError(
                f"run {run_id}'s last iter has exit_reason {actual!r}; "
                f"only no-signal failures can be reopened"
            )
    # Status flip + event append outside the read transaction.
    await set_run_status(self._sm, run_id, "paused", ended=False)
    await self._store.append(
        run_id,
        "pause_requested",
        {
            "question": (
                "Agent ended without a terminal sentinel; relay auto-paused "
                "on reopen. Provide guidance to resume, or close the run."
            ),
        },
    )
```

`set_run_status(..., ended=False)` already clears `ended_at` — confirm by reading the helper (it sits in `relay.orchestrator.lifecycle` or `relay.db.runs`; search if uncertain).

- [ ] **Step 4: Implement the route**

Edit `src/relay/api/runs.py`. Add below the existing `/cancel` and `/close` routes:

```python
@router.post("/runs/{run_id}/reopen", response_model=RunOut)
async def reopen_run(run_id: str, core: CoreDep) -> RunOut:
    """Reopen a failed+no-signal run as paused (WU5 — resilient-iter-close).

    Returns 404 if the run is unknown, 409 if the run is not failed, 409
    if the last iter's exit_reason is not a no-signal variant.
    """
    if await core.get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    try:
        await core.reopen_failed_as_paused(run_id)
    except ValueError as exc:
        raise http_error(exc) from exc
    updated = await core.get_run(run_id)
    if updated is None:  # pragma: no cover
        raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
    return RunOut.model_validate(updated)
```

Verify `http_error` exists in this module (the `/resume` route uses it). The mapping from the three `ValueError` messages above: "unknown run …" → 404, "is not failed" / "last iter has exit_reason" → 409. If `http_error` does not produce 409 for the new messages, extend its mapping or raise `HTTPException` directly with `status_code=409` based on the message prefix.

- [ ] **Step 5: Run the backend tests**

```bash
uv run pytest tests/api/test_reopen_run.py -v
uv run pytest tests/
uv run ruff check .
uv run mypy
```

Expected: PASS.

- [ ] **Step 6: Regenerate the frontend API types**

The backend route added a new operation. Start the dev server in one terminal:

```bash
uv run relay serve
```

In another terminal:

```bash
cd frontend && npm run gen:api
```

Expected: `frontend/src/api/types.ts` now contains a `paths["/api/runs/{run_id}/reopen"]["post"]` entry. Stop the dev server.

- [ ] **Step 7: Commit the backend half**

```bash
git add src/relay/core.py src/relay/api/runs.py tests/api/test_reopen_run.py frontend/src/api/types.ts
git commit -m "api: POST /api/runs/{id}/reopen for failed-no-signal runs (WU5 backend)

Reopens a failed+no-signal run as paused so the operator can resume with
guidance. Eligible exit_reasons: agent_end_no_signal,
agent_end_no_signal_autopause. 404 unknown; 409 not failed; 409 last
iter not no-signal."
```

### Frontend (E2)

- [ ] **Step 8: Write the failing UI test**

Create `frontend/src/components/runs/layout/ReopenButton.spec.ts`:

```typescript
import { describe, it, expect, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import RunRightPane from './RunRightPane.vue'

// Reuse whatever in-test API stub the existing RunRightPane.spec.ts
// uses (look for an existing harness factory in this directory).

const failedNoSignalDetail = {
  id: 'r1',
  status: 'failed',
  iters: [
    { seq: 1, signal_kind: null, exit_reason: 'agent_end_no_signal',
      signal_args: null },
  ],
  // ... other RunDetail fields per the OpenAPI shape ...
}

describe('RunRightPane reopen affordance (WU5)', () => {
  it('shows Reopen as paused button for failed+no-signal run', () => {
    const wrapper = mount(RunRightPane, {
      props: { detail: failedNoSignalDetail },
    })
    expect(wrapper.find('[data-test="reopen-button"]').exists()).toBe(true)
  })

  it('hides Reopen button for failed+timeout run', () => {
    const wrapper = mount(RunRightPane, {
      props: {
        detail: {
          ...failedNoSignalDetail,
          iters: [{ seq: 1, signal_kind: null, exit_reason: 'timeout',
                    signal_args: null }],
        },
      },
    })
    expect(wrapper.find('[data-test="reopen-button"]').exists()).toBe(false)
  })

  it('hides Reopen button for done run', () => {
    const wrapper = mount(RunRightPane, {
      props: {
        detail: { ...failedNoSignalDetail, status: 'done' },
      },
    })
    expect(wrapper.find('[data-test="reopen-button"]').exists()).toBe(false)
  })

  it('calls POST /api/runs/{id}/reopen on click', async () => {
    const fetchSpy = vi.fn().mockResolvedValue({ ok: true, json: async () => ({}) })
    vi.stubGlobal('fetch', fetchSpy)
    const wrapper = mount(RunRightPane, {
      props: { detail: failedNoSignalDetail },
    })
    await wrapper.find('[data-test="reopen-button"]').trigger('click')
    await flushPromises()
    expect(fetchSpy).toHaveBeenCalledWith(
      expect.stringMatching(/\/api\/runs\/r1\/reopen$/),
      expect.objectContaining({ method: 'POST' }),
    )
  })
})
```

Adapt the mount harness and global-fetch stub style to match the existing tests in this directory (e.g. `frontend/src/components/runs/layout/RunRightPane.spec.ts` or similar). Do not introduce a fetch stub if the project uses MSW or openapi-fetch's typed mock — match the established pattern.

- [ ] **Step 9: Run the test to verify it fails**

```bash
cd frontend && npx vitest run src/components/runs/layout/ReopenButton.spec.ts
```

Expected: all FAIL — no reopen button exists.

- [ ] **Step 10: Implement the button in RunRightPane.vue**

Edit `frontend/src/components/runs/layout/RunRightPane.vue`. The existing `failureInfo` computed at lines 92-132 already detects `reason === 'agent_end_no_signal'` and renders a hint block. Add a button inside that hint block (and also for the new `'agent_end_no_signal_autopause'` reason — but only failed runs need the button; autopause runs are already paused).

Inside the template's failure hint render (find the existing `<div class="failure-hint">` or equivalent — read the file to locate), append:

```vue
<button
  v-if="canReopen"
  data-test="reopen-button"
  class="reopen-btn"
  :disabled="reopening"
  @click="onReopen"
>
  {{ reopening ? 'Reopening...' : 'Reopen as paused' }}
</button>
```

In the `<script setup>` block, add (alongside the existing `failureInfo` computed):

```typescript
import { ref, computed } from 'vue'
import { useRouter } from 'vue-router'
// adjust path to match the project's API client wrapper:
import client from '@/api/client'

const router = useRouter()
const reopening = ref(false)

const canReopen = computed(() => {
  if (props.detail.status !== 'failed') return false
  const last = props.detail.iters[props.detail.iters.length - 1] ?? null
  const reason = last?.exit_reason
  return (
    reason === 'agent_end_no_signal' ||
    reason === 'agent_end_no_signal_autopause'
  )
})

async function onReopen(): Promise<void> {
  reopening.value = true
  try {
    const { error } = await client.POST('/api/runs/{run_id}/reopen', {
      params: { path: { run_id: props.detail.id } },
    })
    if (error) {
      // Surface inline; the failureInfo hint block is the natural home.
      console.error('reopen failed', error)
      return
    }
    // Run is now paused — invalidate via the store, no manual reload.
    emit('resumed')
  } finally {
    reopening.value = false
  }
}
```

Match the project's actual API client invocation pattern. Search for `client.POST` or `client.post` usage in the existing codebase to match style:

```bash
cd frontend && grep -rn "client\.POST\|client\.post" src/ | head
```

If the project uses a Pinia Colada mutation (likely — see `useArtifactWriteMutation` referenced in CLAUDE.md), create `useReopenRunMutation` alongside it in `frontend/src/queries/` (look for the existing mutation directory pattern) and use that. The mutation factories should be the model; do not roll a raw `fetch` unless the rest of the project does.

- [ ] **Step 11: Run the frontend test**

```bash
cd frontend && npx vitest run src/components/runs/layout/ReopenButton.spec.ts
```

Expected: PASS.

- [ ] **Step 12: Run the full frontend gate**

```bash
cd frontend && npm run check
```

Expected: PASS (eslint --max-warnings 0 + vue-tsc + vitest).

- [ ] **Step 13: Manual UI smoke test**

Start the dev server (Vite + backend):

```bash
# Terminal 1
uv run relay serve
# Terminal 2
cd frontend && npm run dev
```

Open the dashboard, register a project, run a scenario that lands `failed+agent_end_no_signal` (easiest: temporarily disable WU3 with an env flag, or seed a row via the test harness). Confirm:
- "Reopen as paused" button visible on the failure hint card.
- Clicking it flips the run to paused state in the live UI.
- The `PauseAnswerForm` appears with the recovery question.
- Resume with a one-line answer; the next iter runs.

Stop both dev servers.

- [ ] **Step 14: Commit the frontend half**

```bash
git add frontend/src/components/runs/layout/RunRightPane.vue \
        frontend/src/components/runs/layout/ReopenButton.spec.ts \
        frontend/src/queries/  # if a new mutation file was added
git commit -m "dashboard: Reopen-as-paused button for failed+no-signal runs (WU5 frontend)

Adds a button to the failure-hint card on RunRightPane that calls
POST /api/runs/{id}/reopen. Visible only when status=failed AND the
last iter's exit_reason is a no-signal variant. Matches the existing
PauseAnswerForm flow — operator resumes with a one-line answer."
```

---

## WU6 — ADR + spec.md update

**Files:**
- Modify: `docs/decisions.md` (append ADR-53)
- Modify: `docs/spec.md` §6 (loop close paths)

- [ ] **Step 1: Append ADR-53**

Edit `docs/decisions.md`. Append at the bottom (after ADR-52, around line 3274):

```markdown

## ADR-53 — Resilient iter close: corrective recovery iter + auto-pause fallback for clean+no-signal

**Date:** 2026-06-04.
**Status:** accepted.

### Context

The loop's terminal-sentinel contract (spec.md §6) requires every iter
to close with one of `done` / `handoff` / `pause-for-input` /
`fanout`. When pi's session ends cleanly (`stop_reason == "clean"`)
without one of those at column 0, the loop classifies it as
`agent_end_no_signal` and finalises the run as `failed`.

The 2026-06-04 run on `/Users/john/projects/horizons` (run id
`20260604-174717-09d7`) hit this exact path: pi correctly emitted
`unit-start` / `unit-done` for W0.0, then introduced a WU0.1 proposal
inline and ended its turn with "OK to proceed, or adjust the layout
first?" — conversational text expecting a reply but with no
`pause-for-input` sentinel bracket. The session ended clean; the
loop saw no terminal signal; the run failed. The operator lost the
salvageable state (worktree clean, W0.0 committed, WU0.1 designed).

This failure mode is structural: pi has finished speaking and is
waiting for input. That is *literally* a pause. Failing is the wrong
outcome both ergonomically (loses work) and semantically (the agent
is not failed, it is waiting).

### Decision

The loop's `signal is None` branch is widened into three sub-cases,
discriminated by `outcome.marker_headline`, `outcome.stop_reason`,
and a one-shot `recovery_used` flag local to `run_loop`:

1. **Recoverable, recovery unused** — `marker_headline is None AND
   stop_reason == "clean" AND not recovery_used`. The loop closes
   the iter with `exit_reason="agent_end_no_signal"` (no `failed`
   yet), widens `effective_max` by 1 (the recovery shot is NOT a
   `max_iters` consumption), and re-enters the loop with
   `body = _RECOVERY_BODY` — a `RELAY_RECOVERY_NOTICE` block asking
   the agent to re-emit a closing sentinel. The recovery iter's
   `iter_ended` event carries `recovery_iter: true` so the timeline
   can distinguish it from a normal retry.

2. **Recoverable, recovery already used** — same condition but
   `recovery_used`. Auto-pause: synthesise
   `signal_kind="pause"` + `signal_args={"id": f"autopause-{run_id}-{seq}",
   "question": "...", "next_prompt": "", "review_paths": []}`, close
   the iter, return `LoopResult("paused",
   reason="agent_end_no_signal_autopause", …)`. The shape mirrors the
   chat-mode auto-pause at `loop.py:460-481`; the dashboard's
   `PauseAnswerForm` picks it up unchanged.

3. **Marker-contract violation OR non-clean stop** —
   `marker_headline is not None OR stop_reason != "clean"`. The
   `failed` behaviour stands: pi *tried* to emit a sentinel and got
   it wrong (a real bug to surface), or the harness crashed (real
   failure, not omission). No recovery iter.

In parallel:
- **Preamble nudge.** Every task-mode iter's preamble carries a
  `RELAY_SENTINEL_REMINDER:` line listing the four terminal sentinels
  — a pre-emptive defence so the recovery iter and auto-pause fallback
  are second and third lines, not first.
- **Skill-template nudge.** `phase-3-development.md` gains a
  "Closing sentinel is mandatory" block ruling that conversational
  proposal-then-approval text without a `pause-for-input` bracket is
  a contract violation.
- **Operator-driven escape hatch.** `POST /api/runs/{id}/reopen`
  flips a `failed` run whose last iter's `exit_reason` is one of
  `agent_end_no_signal` / `agent_end_no_signal_autopause` back to
  `paused` (event-store unchanged for the closed iters; one new
  `pause_requested` appended with a recovery question). The
  dashboard `RunRightPane` renders a "Reopen as paused" button on
  the failure hint card for eligible runs.

### Alternatives considered

- **Pattern-match the trailing assistant text** ("OK to proceed?",
  "Awaiting your sign-off") and synthesise an implicit pause.
  Rejected: brittle, false positives on legitimate conversational
  asides inside an iter that DOES close correctly later.
- **Auto-pause on every clean+no-signal without trying recovery
  first.** Rejected: cheap-cost recovery (one extra iter) salvages
  runs the agent can self-correct, without wasting operator
  attention. The recovery iter is bounded one-shot.
- **Operator-only escape hatch (E alone).** Rejected: requires the
  operator to notice and act; the recovery + auto-pause combination
  handles the common case automatically. E is preserved for legacy
  failed runs from before this ADR landed.

### Consequences

- `LoopResult.reason` gains a new value
  `agent_end_no_signal_autopause`, distinct from
  `agent_end_no_signal`. OTel attribute `relay.iter.exit_reason`
  inherits the discrimination.
- `iter_ended` event payload optionally carries
  `recovery_iter: true` for the corrective iter. Schema-additive.
- One existing test
  (`tests/orchestrator/test_loop.py::test_fenced_sentinel_no_real_signal_fails_cleanly`)
  is rewritten — the asserted failure mode no longer exists; the
  rewrite asserts the recovery+autopause path.
- Marker-contract violations and crashes still finalise as
  `failed`; this ADR does NOT relax the loop's discipline for the
  failure modes that represent real bugs.
- Chat-mode unaffected — its existing auto-pause at `loop.py:433-481`
  predates this ADR and follows the same shape (intentional symmetry).
```

- [ ] **Step 2: Update spec.md §6 — the loop section**

Find the §6 close-paths description in `docs/spec.md`. Add a sub-section (or extend the existing one) immediately after the §6.x section that describes terminal signal handling:

```markdown
### 6.x Resilient iter close (ADR-53)

A task-mode iter that ends with `stop_reason == "clean"` and no
terminal sentinel triggers a corrective recovery iter:

1. Iter N closes with `exit_reason="agent_end_no_signal"` (no run
   finalisation).
2. Iter N+1 (the recovery iter) runs with body
   `RELAY_RECOVERY_NOTICE: …` asking the agent to re-emit a closing
   sentinel. Budget extension: +1 over `max_iters`, NOT a
   consumption. `iter_ended.payload.recovery_iter = true` on its
   closing event.
3. If the recovery iter produces a terminal sentinel (`done` /
   `handoff` / `pause-for-input` / `fanout`), the run finalises
   normally.
4. If the recovery iter ALSO produces no terminal sentinel under a
   clean stop, the run lands `paused` with a synthesised
   `pause_requested` (id `autopause-<run_id>-<seq>`) and
   `LoopResult.reason == "agent_end_no_signal_autopause"`. The
   dashboard `PauseAnswerForm` picks it up unchanged.

Marker-contract violations (`marker_headline is not None`) and
non-clean stops (`stop_reason in {"crash"}`) bypass the recovery iter
and finalise as `failed` — these represent real bugs we surface, not
omissions we paper over.

For legacy failed runs from before this contract landed, the dashboard
exposes `POST /api/runs/{id}/reopen` to flip a failed+no-signal run
back to paused.
```

Replace `§6.x` with the next free section number. Update any §6 cross-references that list close paths.

- [ ] **Step 3: Verify both docs render and ADR ordering is correct**

```bash
grep -n "^## ADR-5[0-3]" docs/decisions.md | tail -5
```

Expected: ADR-50 / ADR-51 / ADR-52 / ADR-53 in order.

- [ ] **Step 4: Commit**

```bash
git add docs/decisions.md docs/spec.md
git commit -m "docs(ADR-53): resilient iter close — recovery iter + auto-pause fallback

Documents the WU3+WU4 loop change and the WU5 reopen affordance.
Spec §6 gains a 'Resilient iter close' sub-section describing the
three sub-cases of the no-signal branch."
```

---

## WU7 — Final integration verification

- [ ] **Step 1: Full backend + frontend gate**

```bash
uv run pytest
uv run ruff check .
uv run mypy
cd frontend && npm run check
```

All four MUST pass.

- [ ] **Step 2: Live smoke against pi (gated)**

This is the manual-attested live test per ADR-30. Skip if pi credentials are not configured locally.

```bash
PI_INTEGRATION=1 uv run pytest tests/orchestrator/test_pi_e2e.py -v
```

Then run a real engineering-team scenario that historically no-signalled on the proposal step (the horizons run referenced in ADR-53 is the canonical reproducer; recreate with a minimal scratch project):

```bash
uv run relay serve
# In another terminal: register a project, start an engineering-team run
# whose Phase 3 issues a proposal. Confirm the recovery iter fires and the
# run lands either done (pi self-corrected) or paused (auto-pause).
```

Note the outcome in `journal/2606XX-resilient-iter-close.md`.

- [ ] **Step 3: Update CLAUDE.md "Current state" section**

CLAUDE.md tracks the post-MVP arc list with paragraph blurbs. Append a paragraph after the "Chat-mode arc" section describing the resilient-iter-close arc (mirroring the existing blurbs' density). One paragraph; cite ADR-53; cite the recovery iter + autopause + reopen endpoint; cite the WU3 sub-case sieve as the load-bearing decision (marker headlines still fail).

- [ ] **Step 4: Commit the journal + CLAUDE.md update**

```bash
git add journal/ CLAUDE.md
git commit -m "docs: journal + CLAUDE.md for the resilient-iter-close arc (ADR-53)

Closes the WU1-WU7 sequence. Run loop now self-recovers cleanly on a
clean+no-signal close and auto-pauses on a second consecutive
no-signal; failed runs from before this arc can be reopened from the
dashboard."
```

---

## Spec-coverage self-review (do this AFTER writing the plan, before execution)

- **A (auto-pause on clean+no-signal)** — covered by WU4. Test:
  `test_recovery_iter_also_no_signal_auto_pauses`.
- **B (preamble reminder)** — covered by WU2. Test:
  `test_preamble_contains_run_dir_and_sentinel_reminder`.
- **C (skill template exit checklist)** — covered by WU1. Verification
  is doc re-read (no Python tests for markdown changes per the global
  CLAUDE.md guidance and the engteam skill's own Phase 3 §"Non-code
  work units").
- **D (recovery iter)** — covered by WU3. Tests:
  `test_clean_no_signal_triggers_recovery_iter`,
  `test_marker_violation_skips_recovery_iter`,
  `test_recovery_iter_does_not_consume_max_iters`.
- **E (dashboard reopen)** — covered by WU5. Tests:
  `test_reopen_failed_no_signal_run_lands_paused`,
  `test_reopen_unknown_run_404`, `test_reopen_done_run_409`,
  `test_reopen_failed_timeout_run_409`, plus the four UI cases in
  `ReopenButton.spec.ts`.
- **ADR + spec** — covered by WU6.

A/D ordering (D fires first, A is the fallback): enforced by the
`recovery_used` one-shot guard in WU3 Step 3a-b. The `if
is_recoverable_no_signal and not recovery_used` branch is mutually
exclusive with the `if is_recoverable_no_signal and recovery_used`
branch — no race; no path that pauses without first trying the
recovery iter.

Marker-violation discrimination: enforced by the
`is_recoverable_no_signal = outcome.marker_headline is None and
outcome.stop_reason == "clean"` predicate. Pinned by
`test_marker_violation_skips_recovery_iter` (WU3) and the existing
`test_marker_violation_fails_cleanly` in `tests/orchestrator/test_loop.py`.
