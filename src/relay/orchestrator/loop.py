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
import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select

from relay.db.models import Event
from relay.events import EventStore
from relay.harness import (
    AssistantText,
    Harness,
    HarnessSession,
    SessionStarted,
    SignalConfig,
    SignalEmitted,
    ToolUseEnd,
    ToolUseStart,
)
from relay.harness.signaling import MarkerError, detect_in_text
from relay.harness.signaling.fanout import FanoutParseError
from relay.harness.signaling.sentinels import extract_phase_start
from relay.observability import (
    NOOP_ITER_SPAN,
    NOOP_RUN_SPAN,
    IterSpan,
    IterSpanContext,
    RunSpan,
)
from relay.orchestrator.lifecycle import (
    RunContext,
    close_iter,
    open_iter,
    set_iter_session,
)
from relay.orchestrator.preamble import build_preamble, compose_prompt

__all__ = ["LoopResult", "SessionHandle", "run_loop"]

logger = logging.getLogger(__name__)

_TERMINAL = {"done", "handoff", "pause", "fanout"}
_SIGNAL_CONFIG = SignalConfig(strategy="text_sentinels")
_RECOVERY_BODY = (
    "RELAY_RECOVERY_NOTICE: Your previous turn ended without a terminal\n"
    "sentinel. Relay needs exactly one of `[[engteam:done]]`,\n"
    "`[[engteam:handoff]]`, "
    '`[[engteam:pause-for-input id="..." question="..."]]`,\n'
    "or `[[engteam:fanout]]` at column 0 to close the iter.\n"
    "\n"
    "Re-emit your final state. If you were waiting on operator approval,\n"
    "the correct closing sentinel is `pause-for-input` — bracket the\n"
    "question with `[[engteam:prompt-start]]` / `[[engteam:prompt-end]]`\n"
    "per the engineering-team skill's pause protocol."
)


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
    fanout_payload: dict[str, Any] | None = None
    # ADR-38: opaque OTel context captured from the dispatching iter span.
    # Populated ONLY on the signal.kind=="fanout" branch — all other
    # terminals leave this None. Task 4 threads it through _dispatch_children
    # → _RunState.parent_iter_ctx so each child run-span parents under the
    # fanout iter for cross-run trace continuity.
    fanout_parent_ctx: IterSpanContext | None = None


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
                    except (MarkerError, FanoutParseError) as err:
                        out.marker_headline = (
                            err.headline
                            if isinstance(err, MarkerError)
                            else str(err)
                        )
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
    stop_reason: str,
    messages: list[Any],
    summary: str | None = None,
    recovery_iter: bool = False,
) -> None:
    """Close the iter row + append ``harness_session_ended`` then ``iter_ended``.

    The ``harness_session_ended`` event (ADR-39) lands BEFORE ``iter_ended``
    on every close path: terminal signal, cancelled, timed-out,
    no-signal, crash. Both events share ``iter_id``;
    ``harness_session_ended`` persists pi's verbatim
    ``SessionEnded.messages`` (ADR-18 opaque) and ``stop_reason``, closing
    the ADR-10 invariant gap parked since Phase 7 (ADR-29 captured the
    data for OTel; the event store now gets it too).

    ``exit_reason`` (ADR-48) is mirrored from the loop's iter-close
    decision (``signal`` / ``cancelled`` / ``timeout`` /
    ``agent_end_no_signal`` / ``crash``) so a UsageRow renderer can
    distinguish pi's ``stop_reason="cancelled"`` on a normal
    signal-closed iter (relay's ``finally`` terminated pi BEFORE its
    own ``agent_end``) from a genuinely user-cancelled run. The
    frontend store drops ``iter_id`` from its ``StreamEvent`` shape,
    so the paired ``iter_ended`` event is not reachable client-side —
    duplicating the field on this row is the cheapest path. Same row
    also pairs with the iter row's ``exit_reason`` column (no new
    column needed).
    """
    await store.append(
        run_id,
        "harness_session_ended",
        {
            "stop_reason": stop_reason,
            "messages": messages,
            "summary": summary,
            "exit_reason": exit_reason,
        },
        iter_id=iter_id,
    )
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
    is_chat = ctx.mode == "chat"
    # Task mode (ADR-20 invariant): always None between iters — fresh
    # context per iter is the value prop, pi resume is crash recovery only.
    # Chat mode (ADR-NN, intentional inversion): starts from ctx.resume_session_id
    # (the prior iter's pi_session_id, threaded by resume_run). Each chat-mode
    # loop call runs at most one iter (auto-pauses on session_end), so there
    # is no between-iter carry-forward to maintain inside the loop body.
    last_session_id: str | None = ctx.resume_session_id if is_chat else None

    # ADR-22: a resumed run is guaranteed >=1 post-answer iter even if it
    # paused on its last budgeted iter. For a fresh run (start_seq == 0)
    # effective_max == max_iters, so fresh-run behavior is unchanged.
    effective_max = max(ctx.max_iters, seq + 1)

    # WU3 (resilient-iter-close, ADR-53): a clean stop_reason with no
    # terminal sentinel gets ONE corrective retry — the recovery iter —
    # before WU4's auto-pause fallback kicks in. ``recovery_used``
    # ensures the retry is one-shot; a recovery iter that itself
    # no-signals falls through to WU4 in the same ``signal is None``
    # branch below.
    recovery_used = False
    # WU3 (ADR-53): explicit flag set by the recovery-dispatch branch
    # to tag the *next* iter as the recovery iter. Set to True at
    # `continue` time below, consumed (read + reset) at the top of
    # the loop body. Avoids relying on byte-equality between `body`
    # and `_RECOVERY_BODY` — handoff carry-forward writes
    # agent-authored text into `body`, which could in principle
    # collide with the recovery sentinel literal.
    pending_recovery = False

    # 14e: when the first iter of this loop is a resumed iter, count
    # `artifact_edited` events scoped to the paused predecessor iter so
    # the OTel `relay.iter` span can carry `relay.pause.artifacts_edited_count`.
    # Subsequent iters get `None` (attribute omitted). The count query is
    # one indexed lookup against `events.iter_id`.
    pause_edits_pending: int | None = None
    if ctx.paused_predecessor_iter_id is not None:
        async with sm() as s:
            pause_edits_pending = int(
                await s.scalar(
                    select(func.count())
                    .select_from(Event)
                    .where(
                        Event.iter_id == ctx.paused_predecessor_iter_id,
                        Event.kind == "artifact_edited",
                    )
                )
                or 0
            )

    while seq < effective_max:
        seq += 1
        is_recovery_iter = pending_recovery
        pending_recovery = False
        if is_chat:
            # ADR-NN: chat mode sends the user's message verbatim. The
            # RELAY_* preamble is engteam-skill plumbing (RUN_DIR / PHASE);
            # a conversational pi has no skill loaded and would render the
            # preamble as a noisy prefix on every turn.
            preamble = ""
            full_prompt = body
        else:
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
        #
        # 14e: on the FIRST iter of a resumed run, also pass the
        # pre-computed count of `artifact_edited` events scoped to the
        # paused predecessor iter. The OTel iter span records it as
        # `relay.pause.artifacts_edited_count` (an int; low cardinality).
        # `pause_edits_pending` is consumed once then reset to None so
        # the attribute appears only on the resumed iter's span.
        with otel_run.iter_span(
            seq=seq,
            phase=phase,
            pause_artifacts_edited_count=pause_edits_pending,
        ) as iter_span:
            pause_edits_pending = None
            session = await harness.spawn(
                prompt=full_prompt,
                cwd=ctx.cwd,
                env={},  # PI_AGENT_SDK is injected inside the harness
                signal_config=_SIGNAL_CONFIG,
                resume_from=last_session_id,
                # ADR-NN: chat mode suppresses skill injection — the
                # engteam skill is the *opposite* of what chat mode
                # wants. ``[]`` is distinct from ``None``: ``None`` means
                # "use settings.pi_skill_paths" (task default); ``[]``
                # means "explicit zero skills for this spawn". Pi's own
                # auto-discovery of <cwd>/.pi/skills/ is unaffected.
                skill_paths=[] if is_chat else None,
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
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                    recovery_iter=is_recovery_iter,
                )
                return LoopResult("cancelled", reason="cancelled")
            if outcome.timed_out:
                iter_span.set_exit("timeout")
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind=None, signal_args=None,
                    exit_reason="timeout",
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                    recovery_iter=is_recovery_iter,
                )
                return LoopResult("failed", reason="timeout")

            if is_chat and outcome.stop_reason != "crash":
                # ADR-NN: chat mode never terminates on a sentinel and
                # never fails on agent_end_no_signal. Any non-cancelled,
                # non-timeout, non-crash exit auto-pauses for the next
                # user message. A stray sentinel (pi got confused — chat
                # mode loads no engteam skill, so any sentinel is noise)
                # caused ``_drive_iter`` to break early; its finally then
                # cancelled the pi session, so ``stop_reason`` is now
                # ``"cancelled"`` even though no external cancel fired.
                # Log + ignore + auto-pause. ``outcome.signal`` (if set)
                # already produced a ``signal_emit`` event row that
                # ``_drive_iter`` wrote before breaking — left in place
                # as an honest record of what pi said; relay just
                # doesn't act on it.
                if outcome.signal is not None:
                    logger.warning(
                        "chat-mode iter %d emitted unexpected sentinel "
                        "%r; ignoring (chat mode auto-pauses on "
                        "session_end)",
                        iter_id, outcome.signal.kind,
                    )
                if outcome.marker_headline:
                    logger.warning(
                        "chat-mode iter %d had a marker-contract "
                        "violation (%s); auto-pausing anyway",
                        iter_id, outcome.marker_headline,
                    )
                pause_id = f"chat-{ctx.run_id}-{seq}"
                synth_args: dict[str, Any] = {
                    "id": pause_id,
                    "question": "",
                    "next_prompt": "",
                    "review_paths": [],
                }
                iter_span.set_exit("signal")
                await _finish_iter(
                    store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                    signal_kind="pause", signal_args=synth_args,
                    exit_reason="signal",
                    stop_reason=outcome.stop_reason,
                    messages=outcome.messages,
                    recovery_iter=is_recovery_iter,
                )
                return LoopResult(
                    "paused",
                    reason="signal",
                    question="",
                    next_prompt="",
                    pause_id=pause_id,
                )

            signal = outcome.signal
            if signal is None:
                # WU3 (ADR-53): three sub-cases on no terminal signal.
                #   (1) clean stop, no marker headline, recovery unused:
                #       issue ONE corrective recovery iter (+1 budget,
                #       NOT a max_iters consumption).
                #   (2) clean stop, no marker headline, recovery used:
                #       WU4 will auto-pause here (lands in next WU).
                #   (3) marker-contract violation OR non-clean stop
                #       (crash): existing failed behaviour stands —
                #       pi tried to emit a sentinel and got it wrong,
                #       or the harness crashed. Real bug, not an
                #       omission we should paper over.
                is_recoverable_no_signal = (
                    outcome.marker_headline is None
                    and outcome.stop_reason == "clean"
                )
                if is_recoverable_no_signal and not recovery_used:
                    recovery_used = True
                    iter_span.set_exit("agent_end_no_signal")
                    # `pending_recovery` (not body == _RECOVERY_BODY) is the
                    # canonical way to identify the next iter as a recovery iter.
                    await _finish_iter(
                        store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                        signal_kind=None, signal_args=None,
                        exit_reason="agent_end_no_signal",
                        stop_reason=outcome.stop_reason,
                        messages=outcome.messages,
                        recovery_iter=is_recovery_iter,
                    )
                    effective_max += 1
                    pending_recovery = True
                    body = _RECOVERY_BODY
                    continue
                if is_recoverable_no_signal and recovery_used:
                    # WU4 (ADR-53): the recovery iter (WU3) also produced no
                    # terminal sentinel under a clean stop. Auto-pause instead
                    # of failing — the agent is clearly stuck on something the
                    # operator can unblock. Mirrors the chat-mode synth-pause
                    # shape above. The dashboard's PauseAnswerForm picks it up
                    # unchanged; reason='agent_end_no_signal_autopause'
                    # discriminates from operator-emitted pause-for-input in
                    # telemetry.
                    pause_id = f"autopause-{ctx.run_id}-{seq}"
                    pause_question = (
                        "Agent ended without a terminal sentinel; relay "
                        "auto-paused. Provide guidance to resume, or close "
                        "the run."
                    )
                    autopause_args: dict[str, Any] = {
                        "id": pause_id,
                        "question": pause_question,
                        "next_prompt": "",
                        "review_paths": [],
                    }
                    iter_span.set_exit("agent_end_no_signal")
                    await _finish_iter(
                        store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                        signal_kind="pause", signal_args=autopause_args,
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
                # Sub-case (3): marker-contract violation OR non-clean stop
                # (crash). Pi tried to emit a sentinel and got it wrong, or
                # the harness crashed — a real bug, not an omission to paper
                # over.
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
                    recovery_iter=is_recovery_iter,
                )
                return LoopResult(
                    "failed", reason=reason,
                    summary=outcome.marker_headline,
                )

            iter_span.set_exit("signal")
            summary_val = (
                signal.args.get("summary") if signal.kind == "done" else None
            )
            await _finish_iter(
                store, run_id=ctx.run_id, iter_id=iter_id, seq=seq,
                signal_kind=signal.kind, signal_args=signal.args,
                exit_reason="signal",
                stop_reason=outcome.stop_reason,
                messages=outcome.messages,
                summary=summary_val,
                recovery_iter=is_recovery_iter,
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
            if signal.kind == "fanout":
                # ADR-38: capture the iter span context HERE, inside the
                # iter's `with` block, before it closes. This is the only
                # terminal branch that sets fanout_parent_ctx; all other
                # branches leave it None (the dataclass default). Task 4
                # passes this context to _dispatch_children so each child
                # run-span parents under this dispatching iter span for
                # cross-run trace continuity.
                parent_ctx = iter_span.context
                return LoopResult(
                    "awaiting_children",
                    reason="signal",
                    fanout_payload=signal.args.get("payload"),
                    fanout_parent_ctx=parent_ctx,
                )
            # handoff — carry the compressed prompt; context stays fresh.
            body = signal.args["next_prompt"]
            last_session_id = None

    return LoopResult("failed", reason="max_iters")
