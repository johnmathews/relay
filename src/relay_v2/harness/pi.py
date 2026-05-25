"""PiHarness — the only module that knows pi's JSONL schema (ADR-04).

Mapping is grounded in the de-risking fixtures (spec.md §4.2,
``scratch/pi_derisk_workdir/findings.md``), not speculation:

- ``session`` -> ``SessionStarted``
- ``message_update`` / ``text_delta`` -> accumulate -> ``AssistantText``
- ``message_update`` / ``thinking_delta`` -> accumulate ->
  ``AssistantText(kind="thinking")`` (ADR-18)
- ``tool_execution_start`` -> ``ToolUseStart``
- ``tool_execution_update`` -> ``ToolUseUpdate``
- ``tool_execution_end`` -> ``ToolUseEnd``
- ``agent_end`` -> ``SessionEnded(stop_reason="clean")``
- ``agent_start`` / ``turn_start`` / ``message_*`` / ``turn_end`` and any
  unrecognised type -> consumed internally

OQ-2: ``text_delta`` deltas are accumulated per turn and flushed as one
``AssistantText`` at ``turn_end`` -- concatenated deltas equal
``text_end.content`` in every captured stream. OQ-1: ``agent_end``'s
``messages`` list is passed through verbatim; the harness never
interprets it.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections.abc import AsyncIterator, Iterable, Iterator
from pathlib import Path

from relay_v2.config import Settings, get_settings
from relay_v2.harness.protocol import (
    AssistantText,
    AssistantTextDelta,
    HarnessEvent,
    SessionEnded,
    SessionStarted,
    ToolUseEnd,
    ToolUseStart,
    ToolUseUpdate,
)

__all__ = [
    "PiHarness",
    "PiSession",
    "map_pi_events",
    "pi_version_mismatch_warning",
]

logger = logging.getLogger(__name__)

_VERSION_RE = re.compile(r"(\d+\.\d+\.\d+)")


def pi_version_mismatch_warning(
    reported: str, expected: str
) -> str | None:
    """Return a warning string if the reported pi version differs from
    the pinned one, else ``None`` (OQ-5).

    ``reported`` is raw ``pi --version`` output; the first
    ``MAJOR.MINOR.PATCH`` token is compared. Unparseable output yields a
    warning too — an unknown version is as risky as a wrong one. This is
    advisory only: the caller logs it and continues (the harness never
    aborts on version drift; the event mapper degrades gracefully on
    additive schema changes, and a hard pin lives in `.tool-versions`).
    """
    m = _VERSION_RE.search(reported)
    if m is None:
        return (
            f"could not parse pi version from {reported!r}; "
            f"expected {expected} (see .tool-versions)"
        )
    found = m.group(1)
    if found != expected:
        return (
            f"pi version {found} does not match the pinned {expected} "
            f"(see .tool-versions); event-schema drift is possible"
        )
    return None


class _PiEventMapper:
    """Stateful pi-event -> HarnessEvent translator.

    One instance per session. Accumulates streamed deltas per turn and
    flushes them at the turn boundary so the signaling layer always sees
    whole, turn-complete text (spec.md §5.1).
    """

    def __init__(self) -> None:
        self._seq = 0
        self._turn_seq = 0
        self._text: list[str] = []
        self._thinking: list[str] = []
        # ADR-46 Plan B: monotonic per-turn counter for streamed delta
        # events. Reset at every ``turn_start`` alongside the buffers.
        self._delta_seq = 0
        self._tool_started: dict[str, float] = {}
        self.saw_agent_end = False

    def _next(self) -> tuple[int, float]:
        self._seq += 1
        return self._seq, time.time()

    def _flush_turn(self) -> Iterator[HarnessEvent]:
        if self._thinking:
            seq, ts = self._next()
            yield AssistantText(
                seq=seq,
                ts=ts,
                text="".join(self._thinking),
                turn_seq=self._turn_seq,
                kind="thinking",
            )
            self._thinking.clear()
        if self._text:
            seq, ts = self._next()
            yield AssistantText(
                seq=seq,
                ts=ts,
                text="".join(self._text),
                turn_seq=self._turn_seq,
                kind="text",
            )
            self._text.clear()

    def feed(self, ev: dict[str, object]) -> Iterator[HarnessEvent]:
        kind = ev.get("type")

        if kind == "session":
            seq, ts = self._next()
            yield SessionStarted(
                seq=seq,
                ts=ts,
                session_id=str(ev.get("id", "")),
                cwd=str(ev.get("cwd", "")),
            )

        elif kind == "turn_start":
            self._turn_seq += 1
            self._text.clear()
            self._thinking.clear()
            self._delta_seq = 0

        elif kind == "message_update":
            ame = ev.get("assistantMessageEvent")
            if isinstance(ame, dict):
                sub = ame.get("type")
                if sub == "text_delta":
                    delta = str(ame.get("delta", ""))
                    self._text.append(delta)
                    # ADR-46 Plan B: surface the chunk inline. The
                    # accumulated AssistantText still flushes at
                    # turn_end (ADR-18 concatenation invariant
                    # preserved); deltas are an ADDITIVE ephemeral
                    # signal for the dashboard's live pending row.
                    self._delta_seq += 1
                    seq, ts = self._next()
                    yield AssistantTextDelta(
                        seq=seq,
                        ts=ts,
                        text=delta,
                        turn_seq=self._turn_seq,
                        delta_seq=self._delta_seq,
                        kind="text",
                    )
                elif sub == "thinking_delta":
                    delta = str(ame.get("delta", ""))
                    self._thinking.append(delta)
                    self._delta_seq += 1
                    seq, ts = self._next()
                    yield AssistantTextDelta(
                        seq=seq,
                        ts=ts,
                        text=delta,
                        turn_seq=self._turn_seq,
                        delta_seq=self._delta_seq,
                        kind="thinking",
                    )
                # text/thinking/toolcall start+end framing and any unknown
                # sub-type are consumed internally (ADR-18).

        elif kind == "turn_end":
            yield from self._flush_turn()

        elif kind == "tool_execution_start":
            tool_id = str(ev.get("toolCallId", ""))
            seq, ts = self._next()
            self._tool_started[tool_id] = ts
            args = ev.get("args")
            yield ToolUseStart(
                seq=seq,
                ts=ts,
                tool_id=tool_id,
                name=str(ev.get("toolName", "")),
                args=args if isinstance(args, dict) else {},
            )

        elif kind == "tool_execution_update":
            partial = ev.get("partialResult")
            seq, ts = self._next()
            yield ToolUseUpdate(
                seq=seq,
                ts=ts,
                tool_id=str(ev.get("toolCallId", "")),
                partial_result=partial if isinstance(partial, dict) else {},
            )

        elif kind == "tool_execution_end":
            tool_id = str(ev.get("toolCallId", ""))
            seq, ts = self._next()
            started = self._tool_started.pop(tool_id, ts)
            result = ev.get("result")
            yield ToolUseEnd(
                seq=seq,
                ts=ts,
                tool_id=tool_id,
                result=result if isinstance(result, dict) else {},
                is_error=bool(ev.get("isError", False)),
                duration_ms=max(0, int((ts - started) * 1000)),
            )

        elif kind == "agent_end":
            yield from self._flush_turn()
            self.saw_agent_end = True
            seq, ts = self._next()
            msgs = ev.get("messages")
            yield SessionEnded(
                seq=seq,
                ts=ts,
                messages=list(msgs) if isinstance(msgs, list) else [],
                stop_reason="clean",
            )

        # agent_start, message_start, message_end and any unrecognised
        # top-level type: consumed internally, nothing surfaced.

    def synthesize_end(
        self, stop_reason: str, messages: list[object] | None = None
    ) -> SessionEnded:
        """Terminal event when pi exited without an ``agent_end`` (crash,
        timeout, cancellation). Buffered partial-turn text is discarded:
        an interrupted turn has no complete text to surface."""
        seq, ts = self._next()
        return SessionEnded(
            seq=seq,
            ts=ts,
            messages=messages or [],
            stop_reason=stop_reason,  # type: ignore[arg-type]
        )


def map_pi_events(events: Iterable[dict[str, object]]) -> list[HarnessEvent]:
    """Offline mapping helper -- the harness unit-test entry point.

    Pure: no subprocess, no clock dependence beyond ``ts`` stamping.
    Mirrors exactly what :meth:`PiSession.events` does per line.
    """
    mapper = _PiEventMapper()
    out: list[HarnessEvent] = []
    for ev in events:
        out.extend(mapper.feed(ev))
    return out


class PiSession:
    """One pi ``--mode json`` subprocess (spec.md §4.2, ADR-16)."""

    def __init__(
        self, proc: asyncio.subprocess.Process, session_hint: str = ""
    ) -> None:
        self._proc = proc
        self._mapper = _PiEventMapper()
        self._cancelled = False
        self._final: SessionEnded | None = None
        self.session_id = session_hint

    async def events(self) -> AsyncIterator[HarnessEvent]:
        """Stream normalized events with a one-event ``AssistantText``
        lookahead (Option D, ADR-29).

        pi emits ``…turn_end, agent_end``: the sentinel-bearing text is
        flushed at ``turn_end`` and ``agent_end`` (the only carrier of
        ``messages[].usage``) follows. The orchestrator detects the
        terminal sentinel on that ``AssistantText`` and ``break``s — so
        without lookahead it would never consume ``agent_end`` and
        ``wait()`` would synthesize an empty :class:`SessionEnded`,
        losing token/cost.

        Fix: hold the most recent ``AssistantText`` by exactly one
        event. It is delivered immediately before the *next* mapper
        output (any kind), so external event order is unchanged and the
        event store is unaffected (the orchestrator still breaks before
        ``SessionEnded`` is yielded — no ``agent_end`` row, ADR-10
        contract intact). The win: when that next raw line is
        ``agent_end``, ``self._final`` is captured *before* the held
        text is handed over, so the post-``break`` ``wait()`` returns
        pi's verbatim usage messages. Deterministic — ``agent_end`` is
        consumed in-stream, not raced against process exit.
        """
        assert self._proc.stdout is not None
        pending: AssistantText | None = None
        async for raw in self._proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue  # findings.md: non_json_lines=0; defensive only
            if not isinstance(ev, dict):
                continue
            for out in self._mapper.feed(ev):
                if isinstance(out, SessionStarted) and not self.session_id:
                    self.session_id = out.session_id
                if isinstance(out, SessionEnded):
                    # Capture _final BEFORE the held sentinel text is
                    # delivered, so the orchestrator's post-break
                    # wait() already has the usage payload.
                    self._final = out
                    if pending is not None:
                        yield pending
                        pending = None
                    yield out
                elif isinstance(out, AssistantText):
                    # Hold the latest text; flush any prior one first so
                    # a thinking→text pair within a turn keeps its order.
                    if pending is not None:
                        yield pending
                    pending = out
                else:
                    if pending is not None:
                        yield pending
                        pending = None
                    yield out
        # Stream ended without agent_end (crash/timeout): still deliver
        # the buffered text; wait() synthesizes the terminal event as
        # before.
        if pending is not None:
            yield pending

    async def cancel(self) -> None:
        self._cancelled = True
        if self._proc.returncode is not None:
            return
        self._proc.terminate()
        try:
            await asyncio.wait_for(self._proc.wait(), timeout=5)
        except TimeoutError:
            self._proc.kill()
            await self._proc.wait()

    async def wait(self) -> SessionEnded:
        await self._proc.wait()
        if self._final is not None:
            return self._final
        # No agent_end was seen. Distinguish cancellation from an
        # unexpected exit; timeout vs crash is the orchestrator's call
        # (Phase 2) -- the harness reports the lower-level fact.
        reason = "cancelled" if self._cancelled else "crash"
        self._final = self._mapper.synthesize_end(reason)
        return self._final


class PiHarness:
    """Spawns pi subprocesses and yields :class:`PiSession`s.

    Invocation form per ADR-16 (amends ADR-03): ``--mode json``,
    one subprocess per iter. ``PI_AGENT_SDK=1`` is always injected
    (ADR-09 / findings.md auth path).
    """

    name = "pi"

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._version_checked = False

    async def _maybe_check_version(self) -> None:
        """Best-effort, once per harness: log a warning if the installed
        pi differs from the pin (OQ-5). Never raises — pi may be absent
        (offline/scripted tests) and version drift is non-fatal. Uses the
        same no-shell exec form as :meth:`spawn`."""
        if self._version_checked:
            return
        self._version_checked = True
        try:
            proc = await asyncio.create_subprocess_exec(
                self._settings.pi_bin,
                "--version",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            out, _ = await proc.communicate()
            warning = pi_version_mismatch_warning(
                out.decode(errors="replace"),
                self._settings.pi_expected_version,
            )
            if warning:
                logger.warning("pi version check: %s", warning)
        except Exception as exc:  # noqa: BLE001 - advisory probe only
            logger.debug("pi version probe skipped: %s", exc)

    def _build_argv(
        self, prompt: str, model: str, provider: str, resume_from: str | None
    ) -> list[str]:
        argv = [
            self._settings.pi_bin,
            "-p",
            prompt,
            "--mode",
            "json",
            "--provider",
            provider,
            "--model",
            model,
        ]
        # Bundled skill injection. Pi `--skill` is repeatable and accepts
        # a file or directory; relay points at its bundled engineering-team
        # skill so every spawn sees it regardless of CWD. Pi's own
        # auto-discovery of `<cwd>/.pi/skills/` and `~/.pi/agent/skills/`
        # remains on by default — explicit injection is additive.
        for skill_path in self._settings.pi_skill_paths:
            argv += ["--skill", str(skill_path)]
        if resume_from:
            # Crash recovery only -- never used for inter-iter chaining
            # (CLAUDE.md: fresh context per iter is the value prop).
            argv += ["--session", resume_from]
        return argv

    async def spawn(
        self,
        prompt: str,
        cwd: Path,
        env: dict[str, str],
        signal_config: object,  # SignalConfig — used by the orchestrator (Phase 2)
        resume_from: str | None = None,
    ) -> PiSession:
        await self._maybe_check_version()
        argv = self._build_argv(
            prompt,
            self._settings.pi_model,
            self._settings.pi_provider,
            resume_from,
        )
        full_env = {**os.environ, **env, "PI_AGENT_SDK": "1"}
        # argv list, no shell — not subject to injection.
        # limit= raises the StreamReader buffer above asyncio's 64 KiB
        # default so a large pi JSONL line (tool result, agent_end.messages)
        # does not crash readline() with LimitOverrunError.
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=full_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=self._settings.pi_stdout_limit,
        )
        return PiSession(proc, session_hint=resume_from or "")
