"""``text_sentinels`` signaling strategy (spec.md §5.1, ADR-05).

A faithful Python port of v1's sentinel parser (``relay-v1/bin/relay``'s
awk/jq functions, mirrored by ``relay-v1/tests/test-parsing.sh``). The
grammar and the exact error/repair strings are preserved verbatim — v1's
30 synthetic fixtures are ported to ``test_signaling_sentinels.py`` and
must stay green.

Anti-mention discipline is enforced *upstream* in the harness, not here:
only ``AssistantText`` events with ``kind == "text"`` (never tool inputs,
never ``thinking``; ADR-18) are ever fed to this parser, exactly as v1's
``jq`` filter stripped tool-input content before parsing. This module
operates on that already-isolated text.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any

from relay.harness.protocol import SignalConfig, SignalEmitted

if TYPE_CHECKING:
    from relay.harness.signaling.fanout import FanoutPayload

__all__ = [
    "MarkerError",
    "count_closing_sentinels",
    "extract_handoff_prompt",
    "extract_pause_prompt",
    "validate_done_no_prompt_markers",
    "extract_pause_question",
    "extract_pause_id",
    "extract_pause_review_path",
    "extract_pause_review_paths",
    "extract_phase_start",
    "extract_fanout_payload",
    "detect_in_text",
]

# Line-anchored matchers (the matcher reads raw lines; a sentinel counts
# only at column 0, trailing whitespace permitted — see sentinels.md).
_DONE_RE = re.compile(r"^\[\[engteam:done\]\][ \t]*$")
_HANDOFF_RE = re.compile(r"^\[\[engteam:handoff\]\][ \t]*$")
_PAUSE_RE = re.compile(r"^\[\[engteam:pause-for-input[ \t]")
_PROMPT_START_RE = re.compile(r"^\[\[engteam:prompt-start\]\][ \t]*$")
_PROMPT_END_RE = re.compile(r"^\[\[engteam:prompt-end\]\][ \t]*$")
_PROMPT_MARKER_RE = re.compile(r"^\[\[engteam:prompt-(start|end)\]\][ \t]*$")
_PHASE_START_RE = re.compile(r"^\[\[engteam:phase-start[ \t]")
_UNIT_START_RE = re.compile(r"^\[\[engteam:unit-start[ \t]")
_UNIT_DONE_RE = re.compile(r"^\[\[engteam:unit-done[ \t]")
_UNIT_ABANDONED_RE = re.compile(r"^\[\[engteam:unit-abandoned[ \t]")
_BLANK_RE = re.compile(r"^[ \t]*$")
_FANOUT_RE = re.compile(r"^\[\[engteam:fanout\]\][ \t]*$")
_FANOUT_START_RE = re.compile(r"^\[\[engteam:fanout-start\]\][ \t]*$")
_FANOUT_END_RE = re.compile(r"^\[\[engteam:fanout-end\]\][ \t]*$")


def _marker_repair_recipe(close: str) -> str:
    return (
        "\nThe closing sentinel must be preceded by a marker pair. "
        "Required shape\n(every marker line must be at column 0):\n\n"
        "    [[engteam:prompt-start]]\n"
        "    <next-session prompt body — any markdown, including fenced "
        "code blocks>\n"
        "    [[engteam:prompt-end]]\n\n"
        f"    {close}\n\n"
        "If your iter ended with a triple-backtick fenced block "
        f"immediately\nbefore {close}, that is the pre-2026-05-17 "
        "convention and the driver\nno longer accepts it. Wrap the "
        "prompt body in the marker pair above.\n\n"
        "See: skills/engineering-team/references/sentinels.md\n"
    )


def _done_repair_recipe() -> str:
    return (
        "\n[[engteam:done]] takes no prompt body. Required shape:\n\n"
        "    [[engteam:done]]\n\n"
        "Remove any [[engteam:prompt-start]] / [[engteam:prompt-end]] "
        "markers\npreceding the sentinel.\n\n"
        "See: skills/engineering-team/references/sentinels.md\n"
    )


class MarkerError(Exception):
    """Raised when the prompt-marker contract is violated.

    ``str(err)`` is the one-line headline followed by the multi-line
    repair recipe — mirroring v1's stderr so the ported substring
    assertions match either part.
    """

    def __init__(self, headline: str, recipe: str) -> None:
        super().__init__(headline + "\n" + recipe)
        self.headline = headline


def count_closing_sentinels(text: str) -> dict[str, int]:
    """Count line-anchored closing sentinels. The driver bails if the
    total != 1; that policy is the orchestrator's (Phase 2). Here we
    just report the raw counts, exactly as v1's ``grep -c`` did."""
    done = handoff = pause = fanout = 0
    for line in text.split("\n"):
        if _DONE_RE.match(line):
            done += 1
        elif _HANDOFF_RE.match(line):
            handoff += 1
        elif _PAUSE_RE.match(line):
            pause += 1
        elif _FANOUT_RE.match(line):
            fanout += 1
    return {"done": done, "handoff": handoff, "pause": pause, "fanout": fanout}


def _extract_marker_prompt(
    label: str, close_disp: str, close_re: re.Pattern[str], text: str
) -> str:
    """Port of v1's ``_extract_marker_prompt`` awk routine, preserving
    its exact decision order and error strings."""
    lines = text.split("\n")
    total = len(lines)

    close_line = 0  # 1-indexed, mirroring the awk source
    for i in range(total, 0, -1):
        if close_re.match(lines[i - 1]):
            close_line = i
            break
    if close_line == 0:
        raise MarkerError(
            f"{label}: no {close_disp} found", _marker_repair_recipe(close_disp)
        )

    first_non_blank = 0
    for i in range(close_line - 1, 0, -1):
        if _BLANK_RE.match(lines[i - 1]):
            continue
        first_non_blank = i
        break

    if first_non_blank == 0:
        raise MarkerError(
            f"{label}: no [[engteam:prompt-end]] preceding {close_disp}",
            _marker_repair_recipe(close_disp),
        )

    if _PROMPT_END_RE.match(lines[first_non_blank - 1]):
        end_line = first_non_blank
    else:
        seen_end = any(
            _PROMPT_END_RE.match(lines[i - 1]) for i in range(1, close_line)
        )
        if seen_end:
            raise MarkerError(
                f"{label}: content between [[engteam:prompt-end]] and "
                "closing sentinel",
                _marker_repair_recipe(close_disp),
            )
        raise MarkerError(
            f"{label}: no [[engteam:prompt-end]] preceding {close_disp}",
            _marker_repair_recipe(close_disp),
        )

    start_line = 0
    for i in range(end_line - 1, 0, -1):
        if _PROMPT_END_RE.match(lines[i - 1]):
            raise MarkerError(
                f"{label}: content between [[engteam:prompt-end]] and "
                "closing sentinel",
                _marker_repair_recipe(close_disp),
            )
        if _PROMPT_START_RE.match(lines[i - 1]):
            start_line = i
            break
    if start_line == 0:
        raise MarkerError(
            f"{label}: [[engteam:prompt-end]] without matching "
            "[[engteam:prompt-start]]",
            _marker_repair_recipe(close_disp),
        )

    return "\n".join(lines[start_line : end_line - 1])


def extract_handoff_prompt(text: str) -> str:
    """Body between the marker pair preceding ``[[engteam:handoff]]``."""
    return _extract_marker_prompt(
        "extract_handoff_prompt", "[[engteam:handoff]]", _HANDOFF_RE, text
    )


def extract_pause_prompt(text: str) -> str:
    """Body between the marker pair preceding the pause sentinel."""
    return _extract_marker_prompt(
        "extract_pause_prompt", "[[engteam:pause-for-input]]", _PAUSE_RE, text
    )


def validate_done_no_prompt_markers(text: str) -> None:
    """Raise if a prompt-marker pair precedes ``[[engteam:done]]``.
    ``done`` carries no prompt body (sentinels.md)."""
    saw_marker = False
    for line in text.split("\n"):
        if _PROMPT_MARKER_RE.match(line):
            saw_marker = True
        elif _DONE_RE.match(line):
            if saw_marker:
                raise MarkerError(
                    "[[engteam:done]] cannot have prompt markers "
                    "(found prompt-start/end pair preceding)",
                    _done_repair_recipe(),
                )
            return


_Q_RE = re.compile(r'question="((?:[^"\\]|\\.)*)"')
_ID_RE = re.compile(r'id="([^"]*)"')
_PHASE_ATTR_RE = re.compile(r'phase="([^"]*)"')


def extract_pause_question(text: str) -> str:
    """First pause sentinel's ``question`` attribute, ``\\"`` unescaped."""
    for line in text.split("\n"):
        if _PAUSE_RE.match(line):
            m = _Q_RE.search(line)
            if m:
                return m.group(1).replace('\\"', '"')
            return ""
    return ""


def extract_pause_id(text: str) -> str:
    """First pause sentinel's ``id`` attribute."""
    for line in text.split("\n"):
        if _PAUSE_RE.match(line):
            m = _ID_RE.search(line)
            return m.group(1) if m else ""
    return ""


_REVIEW_PATH_RE = re.compile(r'review_path="((?:[^"\\]|\\.)*)"')


_REVIEW_PATH_REPAIR = (
    "\nThe optional review_path attribute on [[engteam:pause-for-input]]"
    " is a path RELATIVE to $RELAY_RUN_DIR (the run's artifacts dir,\n"
    "<project_root>/.relay/runs/<run_id>/). It must not be empty,\n"
    "absolute, contain '..', or carry a NUL byte. Examples:\n\n"
    '    review_path="improvement-plan.md"\n'
    '    review_path="discussions/notes.md"\n\n'
    "Omit the attribute entirely if the pause does not require an\n"
    "editable artifact (the resumed run still re-reads any file your\n"
    "next_prompt body names).\n\n"
    "See: skills/engineering-team/pi/references/sentinels.md\n"
)


def extract_pause_review_paths(text: str) -> list[str]:
    """All ``review_path`` attribute values on the first pause sentinel
    line, in order. Empty list when the attribute is absent. Per ADR-41
    (14f) the attribute may repeat on the same pause line to declare
    multiple files for review. Each value is validated independently via
    :func:`_validate_review_path` (empty / absolute / ``..``-bearing /
    NUL byte → :class:`MarkerError` naming the offending value). Per
    ADR-40 each value is interpreted relative to ``$RELAY_RUN_DIR`` at
    write time; this parser performs syntactic validation only."""
    for line in text.split("\n"):
        if _PAUSE_RE.match(line):
            values: list[str] = []
            for m in _REVIEW_PATH_RE.finditer(line):
                value = m.group(1).replace('\\"', '"')
                _validate_review_path(value)
                values.append(value)
            return values
    return []


def extract_pause_review_path(text: str) -> str | None:
    """Back-compat shim for the 14b single-value extractor (ADR-41).

    Returns the first ``review_path`` value or ``None`` when the
    attribute is absent. Prefer :func:`extract_pause_review_paths` for
    new callers."""
    paths = extract_pause_review_paths(text)
    return paths[0] if paths else None


def _validate_review_path(value: str) -> None:
    if value == "":
        raise MarkerError(
            "extract_pause_review_path: review_path is empty",
            _REVIEW_PATH_REPAIR,
        )
    if "\x00" in value:
        raise MarkerError(
            "extract_pause_review_path: review_path contains NUL byte",
            _REVIEW_PATH_REPAIR,
        )
    if value.startswith("/") or PurePosixPath(value).is_absolute():
        raise MarkerError(
            f"extract_pause_review_path: review_path {value!r} is "
            "absolute (must be relative to $RELAY_RUN_DIR)",
            _REVIEW_PATH_REPAIR,
        )
    parts = PurePosixPath(value).parts
    if any(part == ".." for part in parts):
        raise MarkerError(
            f"extract_pause_review_path: review_path {value!r} contains "
            "'..' (path traversal not allowed)",
            _REVIEW_PATH_REPAIR,
        )


def extract_phase_start(text: str) -> str:
    """Value of the *last* ``phase-start`` sentinel; ``""`` if none.
    Last-wins matches the driver, which writes the final phase seen."""
    last = ""
    for line in text.split("\n"):
        if _PHASE_START_RE.match(line):
            m = _PHASE_ATTR_RE.search(line)
            if m:
                last = m.group(1)
    return last


def extract_fanout_payload(text: str) -> FanoutPayload:
    """Extract and validate the JSON between ``[[engteam:fanout-start]]``
    and ``[[engteam:fanout-end]]`` in the turn containing ``[[engteam:fanout]]``.

    Raises :class:`MarkerError` when the block is structurally absent.
    Raises :class:`~relay.harness.signaling.fanout.FanoutParseError`
    on invalid JSON or a payload that fails ``FanoutPayload`` validation.
    """
    import json as _json

    from pydantic import ValidationError

    from relay.harness.signaling.fanout import FanoutParseError, FanoutPayload

    _REPAIR = (
        "\n[[engteam:fanout]] requires a JSON block between "
        "[[engteam:fanout-start]] and [[engteam:fanout-end]]:\n\n"
        "    [[engteam:fanout-start]]\n"
        '    {"children": [{"role": "...", "prompt": "..."}],\n'
        '     "join_prompt": "..."}\n'
        "    [[engteam:fanout-end]]\n\n"
        "    [[engteam:fanout]]\n\n"
        "See: skills/engineering-team/references/sentinels.md\n"
    )

    lines = text.split("\n")

    end_line = 0
    for i in range(len(lines), 0, -1):
        if _FANOUT_END_RE.match(lines[i - 1]):
            end_line = i
            break
    if end_line == 0:
        raise MarkerError(
            "extract_fanout_payload: no [[engteam:fanout-end]] found",
            _REPAIR,
        )

    start_line = 0
    for i in range(end_line - 1, 0, -1):
        if _FANOUT_START_RE.match(lines[i - 1]):
            start_line = i
            break
    if start_line == 0:
        raise MarkerError(
            "extract_fanout_payload: no [[engteam:fanout-start]] found "
            "before [[engteam:fanout-end]]",
            _REPAIR,
        )

    body = "\n".join(lines[start_line : end_line - 1]).strip()
    try:
        raw = _json.loads(body)
    except _json.JSONDecodeError as exc:
        raise FanoutParseError(
            f"fanout payload is not valid JSON: {exc}\n\nBody was:\n{body}"
        ) from exc

    try:
        return FanoutPayload.model_validate(raw)
    except ValidationError as exc:
        raise FanoutParseError(
            f"fanout payload failed validation: {exc}"
        ) from exc


def _first_attr(line: str, name: str) -> str:
    m = re.search(rf'{name}="((?:[^"\\]|\\.)*)"', line)
    return m.group(1).replace('\\"', '"') if m else ""


def detect_in_text(text: str, config: SignalConfig) -> SignalEmitted | None:
    """Detect a signal in one turn's accumulated assistant text.

    Returns the *terminal* signal (``done`` / ``handoff`` / ``pause``)
    when present — that is what closes an iter (spec.md §6). Non-closing
    sentinels (``phase_start``, ``unit_*``) are surfaced too so the
    orchestrator can record them as events; the terminal signal wins if
    both are present in the same text.

    Marker-contract violations propagate as :class:`MarkerError` rather
    than being swallowed — the orchestrator decides how to fail the run.
    """
    if config.strategy != "text_sentinels":
        return None

    counts = count_closing_sentinels(text)
    if counts["done"]:
        validate_done_no_prompt_markers(text)
        return SignalEmitted(kind="done", args={})
    if counts["handoff"]:
        return SignalEmitted(
            kind="handoff", args={"next_prompt": extract_handoff_prompt(text)}
        )
    if counts["pause"]:
        args: dict[str, Any] = {
            "next_prompt": extract_pause_prompt(text),
            "question": extract_pause_question(text),
            "id": extract_pause_id(text),
        }
        # 14f / ADR-41: write the plural key. Readers (write endpoint,
        # dashboard) fall back to the legacy scalar ``review_path`` iff
        # the plural key is absent — that handles iters paused under
        # 14a–14d that survive a process restart into the 14f code.
        review_paths = extract_pause_review_paths(text)
        if review_paths:
            args["review_paths"] = review_paths
        return SignalEmitted(kind="pause", args=args)
    if counts.get("fanout"):
        # FanoutParseError and MarkerError propagate to the loop's
        # _drive_iter catch clause (loop.py — Task 6).
        payload = extract_fanout_payload(text)
        return SignalEmitted(
            kind="fanout",
            args={"payload": payload.model_dump()},
        )

    for line in text.split("\n"):
        if _UNIT_DONE_RE.match(line):
            return SignalEmitted(
                kind="unit_done",
                args={
                    "id": _first_attr(line, "id"),
                    "title": _first_attr(line, "title"),
                },
            )
        if _UNIT_ABANDONED_RE.match(line):
            return SignalEmitted(
                kind="unit_abandoned",
                args={
                    "id": _first_attr(line, "id"),
                    "reason": _first_attr(line, "reason"),
                },
            )
        if _UNIT_START_RE.match(line):
            return SignalEmitted(
                kind="unit_start",
                args={
                    "id": _first_attr(line, "id"),
                    "title": _first_attr(line, "title"),
                },
            )
    phase = extract_phase_start(text)
    if phase:
        return SignalEmitted(kind="phase_start", args={"phase": phase})
    return None
