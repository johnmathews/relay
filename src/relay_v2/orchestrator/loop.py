"""The chained-iter run loop (spec.md §6 — canonical).

The ``while`` loop below mirrors spec.md §6 one-to-one: fresh harness
session per iter, signal detection at turn boundaries, terminal signal
closes the iter, ``handoff`` carries a compressed prompt to the next
iter, ``last_session_id`` is *always* ``None`` between iters (fresh
context per iter is relay's whole value proposition — pi resume is
crash-recovery only; CLAUDE.md invariant).

Production concerns spec.md §6's pseudocode elides — per-iter wall-clock
timeout, external cancellation, marker-contract violations, phase
carry-forward, and per-iter event recording — are isolated in
``_drive_iter`` so the loop stays as readable as the spec intends.

Run-level status and run-level events (``run_started`` / ``run_ended`` /
``pause_requested``) are :class:`RelayCore`'s job; the loop returns a
:class:`LoopResult` describing how the run terminated. Iter-level events
(``iter_started`` / ``iter_ended`` / ``signal_emit``) and harness events
are emitted here — they are intrinsically per-iter.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from relay_v2.events import EventStore
from relay_v2.harness import (
    AssistantText,
    Harness,
    HarnessSession,
    SessionStarted,
    SignalConfig,
    SignalEmitted,
    ToolUseEnd,
    ToolUseStart,
)
from relay_v2.harness.signaling import MarkerError, detect_in_text
from relay_v2.harness.signaling.sentinels import extract_phase_start
from relay_v2.observability import (
    NOOP_ITER_SPAN,
    NOOP_RUN_SPAN,
    IterSpan,
    RunSpan,
)
from relay_v2.orchestrator.lifecycle import (
    RunContext,
    close_iter,
    open_iter,
    set_iter_session,
)
from relay_v2.orchestrator.preamble import build_preamble, compose_prompt

__all__ = ["LoopResult", "SessionHandle", "run_loop"]

_TERMINAL = {"done", "handoff", "pause"}
_SIGNAL_CONFIG = SignalConfig(strategy="text_sentinels")


@dataclass
class LoopResult:
    """How the run terminated. ``RelayCore`` maps this to the run's final
    status + the closing run-level event."""

    status: str  # 'done' | 'failed' | 'paused' | 'cancelled'
    reason: str  # iter exit_reason / 'max_iters'
    summary: str | None = None
    question: str | None = None
    next_prompt: str | None = None
    pause_id: str | None = None


@dataclass
class SessionHandle:
    """Lets :class:`RelayCore` reach the in-flight session to cancel it
    without the loop having to poll a flag mid-stream."""

    session: HarnessSession | None = None


@dataclass
class _IterOutcome:
    signal: SignalEmitted | None = None
    marker_headline: str | None = None
    timed_out: bool = False
    cancelled: bool = False
    stop_reason: str = "clean"
    text_parts: list[str] = field(default_factory=list)
    # True once _drive_iter has already written a signal_emit for a
    # phase_start, so the carry-forward path doesn't double-record it.
    phase_start_emitted: bool = False
    # SessionEnded.messages, captured for the OTel iter span's GenAI/
    # usage attributes (ADR-29). The event store is unaffected — this is
    # a read-only mirror of data already persisted via store_harness_event.
    messages: list[Any] = field(default_factory=list)

    @property
    def text(self) -> str:
        return "\n".join(self.text_parts)


async def _drive_iter(
    ctx: RunContext,
    session: HarnessSession,
    iter_id: int,
    store: EventStore,
    cancel_event: asyncio.Event,
    otel_iter: IterSpan = NOOP_ITER_SPAN,
) -> _IterOutcome:
    """Stream one iter's events: persist each, detect signals at turn
    boundaries, honour the per-iter timeout and external cancellation."""
    out = _IterOutcome()
    by_turn: dict[int, list[str]] = {}
    # ToolUseStart carries name/args; ToolUseEnd carries duration. Buffer
    # the start by tool_id so the OTel tool_call span (ADR-29) gets the
    # name and an accurate start→end window. Pure mirror — control flow
    # and the event-store writes below are unchanged.
    tool_starts: dict[str, ToolUseStart] = {}
    # try/finally is load-bearing: if the run task is cancelled (aclose()
    # shutdown) CancelledError unwinds straight through asyncio.timeout;
    # without the finally the pi subprocess would leak (no terminate()).
    try:
        try:
            async with asyncio.timeout(ctx.iter_timeout):
                async for ev in session.events():
                    await store.store_harness_event(
                        ctx.run_id, iter_id, ev
                    )
                    if isinstance(ev, SessionStarted) and ev.session_id:
                        await set_iter_session(
                            store.sessionmaker, iter_id, ev.session_id
                        )
                    if isinstance(ev, ToolUseStart):
                        tool_starts[ev.tool_id] = ev
                    elif isinstance(ev, ToolUseEnd):
                        st = tool_starts.pop(ev.tool_id, None)
                        otel_iter.record_tool_call(
                            name=st.name if st else "unknown",
                            tool_id=ev.tool_id,
                            is_error=ev.is_error,
                            duration_ms=ev.duration_ms,
                            start_ts=st.ts if st else ev.ts,
                            end_ts=ev.ts,
                        )
                    if not (
                        isinstance(ev, AssistantText) and ev.kind == "text"
                    ):
                        continue
                    # The mapper flushes exactly one text AssistantText
                    # per turn at turn_end, so this *is* the turn
                    # boundary — no detection on streaming deltas
                    # (spec.md §6 risk note). Guard against a
                    # spec-violating second flush of the same turn so a
                    # malformed/future harness can't double-fire signals.
                    if ev.turn_seq in by_turn:
                        continue
                    by_turn[ev.turn_seq] = [ev.text]
                    turn_text = ev.text
                    out.text_parts.append(ev.text)
                    try:
                        sig = detect_in_text(turn_text, _SIGNAL_CONFIG)
                    except MarkerError as err:
                        out.marker_headline = err.headline
                        break
                    if sig is None:
                        continue
                    await store.append(
                        ctx.run_id,
                        "signal_emit",
                        {"kind": sig.kind, "args": sig.args},
                        iter_id=iter_id,
                    )
                    if sig.kind == "phase_start":
                        out.phase_start_emitted = True
                    if sig.kind in _TERMINAL:
                        out.signal = sig
                        break
                    # Non-closing (phase_start / unit_*): recorded,
                    # iter continues.
        except TimeoutError:
            out.timed_out = True
        if cancel_event.is_set():
            out.cancelled = True
    finally:
        await session.cancel()
        result = await session.wait()
        out.stop_reason = result.stop_reason
        # Mirror only: pi's verbatim messages already persisted via
        # store_harness_event above; copied here purely for the OTel
        # iter span's usage attributes (ADR-18 / ADR-29).
        out.messages = list(result.messages)
    return out


async def _finish_iter(
    store: EventStore,
    *,
    run_id: str,
    iter_id: int,
    seq: int,
    signal_kind: str | None,
    signal_args: dict[str, Any] | None,
    exit_reason: str,
) -> None:
    """Close the iter row + append the paired ``iter_ended`` event."""
    await close_iter(
        store.sessionmaker,
        iter_id,
        signal_kind=signal_kind,
        signal_args=signal_args,
        exit_reason=exit_reason,
    )
    await store.append(
        run_id,
        "iter_ended",
        {"seq": seq, "signal_kind": signal_kind, "exit_reason": exit_reason},
        iter_id=iter_id,
    )


async def run_loop(
    ctx: RunContext,
    *,
    harness: Harness,
    store: EventStore,
    cancel_event: asyncio.Event,
    session_handle: SessionHandle,
    otel_run: RunSpan = NOOP_RUN_SPAN,
) -> LoopResult:
    sm = store.sessionmaker
    seq = ctx.start_seq
    phase = ctx.phase
    body = ctx.body
    last_session_id: str | None = None  # always None between iters

    # ADR-22: a resumed run is guaranteed >=1 post-answer iter even if it
    # paused on its last budgeted iter. For a fresh run (start_seq == 0)
    # effective_max == max_iters, so fresh-run behavior is unchanged.
    effective_max = max(ctx.max_iters, seq + 1)

    while seq < effective_max:
        seq += 1
        preamble = build_preamble(ctx.run_dir, phase)
        full_prompt = compose_prompt(ctx.run_dir, phase, body)
        iter_id = await open_iter(
            sm,
            run_id=ctx.run_id,
            seq=seq,
            phase=phase,
            prompt=full_prompt,
            preamble=preamble,
        )
        await store.append(
            ctx.run_id,
            "iter_started",
            {"seq": seq, "prompt": full_prompt, "preamble": preamble,
             "phase": phase},
            iter_id=iter_id,
        )

        # ADR-29: one relay.iter span per iteration, child of the run
        # span, attribute relay.iter_seq == this iter's `seq` (== the
        # iters table seq) so a Langfuse trace lines up with the
        # dashboard timeline. The `with` only wraps — every return /
        # the handoff continue below behaves exactly as before; the
        # span just ends on the way out.
        with otel_run.iter_span(seq=seq, phase=phase) as iter_span:
            session = await harness.spawn(
                prompt=full_prompt,
                cwd=ctx.cwd,
                env={},  # PI_AGENT_SDK is injected inside the harness
                signal_config=_SIGNAL_CONFIG,
                resume_from=last_session_id,
            )
            session_handle.session = session
            outcome = await _drive_iter(
                ctx, session, iter_id, store, cancel_event, iter_span
            )
            session_handle.session = None
            # GenAI/usage from pi's verbatim messages (ADR-18); set only
            # when present, never zero-filled. Mirror only.
            iter_span.set_usage(outcome.messages)

            # Phase carry-forward: the *last* phase-start of the iter
            # wins (sentinels.md), independent of which signal closed it.
            seen = extract_phase_start(outcome.text)
            if seen:
                phase = seen
                (ctx.run_dir / "phase").write_text(phase)
                # When a terminal signal shared the turn with the
                # phase-start, detect_in_text returned the terminal and
                # _drive_iter never emitted the phase_start event.
                # Record it now so the timeline/replay sees the
                # transition.
                if not outcome.phase_start_emitted:
                    await store.append(
                        ctx.run_id,
                        "signal_emit",
                        {"kind": "phase_start", "args": {"phase": seen}},
                        iter_id=iter_id,
                    )

            if outcome.cancelled:
                iter_span.set_exit("cancelled")
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=None,
                    exit_reason="cancelled",
                )
                return LoopResult("cancelled", reason="cancelled")
            if outcome.timed_out:
                iter_span.set_exit("timeout")
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=None,
                    exit_reason="timeout",
                )
                return LoopResult("failed", reason="timeout")

            signal = outcome.signal
            if signal is None:
                # No usable closing signal: a clean agent_end with
                # nothing, a marker-contract violation, or a crash. The
                # first two are spec.md §3.1's 'agent_end_no_signal'; a
                # crash keeps its own reason. (A fenced/indented
                # sentinel never matched at column 0, so it lands here
                # — the plan.md fenced-block case.)
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
                )
                return LoopResult(
                    "failed", reason=reason,
                    summary=outcome.marker_headline,
                )

            iter_span.set_exit("signal")
            await _finish_iter(
                store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                signal_kind=signal.kind, signal_args=signal.args,
                exit_reason="signal",
            )
            if signal.kind == "done":
                return LoopResult(
                    "done", reason="signal",
                    summary=signal.args.get("summary"),
                )
            if signal.kind == "pause":
                return LoopResult(
                    "paused",
                    reason="signal",
                    question=signal.args.get("question", ""),
                    next_prompt=signal.args.get("next_prompt", ""),
                    pause_id=signal.args.get("id", ""),
                )
            # handoff — carry the compressed prompt; context stays fresh.
            body = signal.args["next_prompt"]
            last_session_id = None

    return LoopResult("failed", reason="max_iters")
