# Plan — Phase 9b (fanout dispatch: sentinel parsing + child-run creation)

**Status:** ready to execute
**Date:** 2026-05-21
**Source proposal:** `docs/proposals/parallel-iters-fanout-join.md` (sub-phase 9b)
**Predecessor:** `docs/plans/2026-05-21-fanout-join-9a.md` (merged as 4ebb1f8)
**Successor:** 9c (synthesizer iter + parent resume), 9d (runtime cancel cascade), 9e (dashboard), 9f (OTel)

## Goal

Parse the `[[engteam:fanout-start]] … [[engteam:fanout-end]]` marker pair emitted by an iter, validate its JSON payload via a Pydantic model, spawn N child runs with `parent_run_id` set, and transition the parent run to `awaiting_children`. Children run independently to terminal states. The parent stays in `awaiting_children` until 9c adds the synthesizer iter. No join logic in this phase.

## Architecture

**Concurrency cap: Option A — `asyncio.Semaphore` in the supervisor.** All N child run rows are created immediately at dispatch time as ordinary `running` rows, swept by the existing orphan-recovery machinery on restart — no new persistent intermediate state. A shared `asyncio.Semaphore(max_fanout_concurrent)` in the supervisor gates how many child tasks actually execute concurrently; children whose slot is unavailable sit in the supervisor queue as `running` rows not yet picked up. This is restart-safe (swept on restart like any running orphan), fair across parents (one pool shared by all), and requires zero new DB state or startup sweep. Option B (hold excess children in a pending queue) would require a new persistent queue that must survive restart — replicating the exact gap 9a closed for `awaiting_children`. ADR-35 records this decision.

**Tech stack:** No new runtime dependencies. Pydantic v2 (already present) validates the fanout JSON payload inline in the signaling package.

## File map

| file | action | one-line responsibility |
|---|---|---|
| `src/relay_v2/harness/signaling/fanout.py` | create | `FanoutChild` + `FanoutPayload` Pydantic models; `FanoutParseError` |
| `src/relay_v2/harness/signaling/sentinels.py` | modify | add `_FANOUT_RE/START/END_RE`; extend `count_closing_sentinels`; add `extract_fanout_payload`; extend `detect_in_text` |
| `src/relay_v2/harness/signaling/__init__.py` | modify | re-export `FanoutPayload`, `FanoutParseError`, `extract_fanout_payload` |
| `src/relay_v2/orchestrator/loop.py` | modify | add `"fanout"` to `_TERMINAL`; `fanout_payload` field on `LoopResult`; catch `FanoutParseError` in `_drive_iter`; add `fanout` return path in `run_loop` |
| `src/relay_v2/orchestrator/lifecycle.py` | modify | `parent_run_id` on `RunContext` + `create_run`; `parent_worktree_path` on `provision_workspace` |
| `src/relay_v2/core.py` | modify | `_fanout_sem` + init; gate child tasks in `_supervise`; `_fanout_depth`; `_dispatch_children`; `_apply_result` awaiting_children branch; `start_run` parent_run_id param |
| `src/relay_v2/config.py` | modify | `max_fanout_depth: int = 2` and `max_fanout_concurrent: int = 4` |
| `docs/spec.md` | modify | §3.1 signal_kind comment; §5.1 fanout note; §12 fanout sentinel grammar |
| `docs/decisions.md` | modify | append ADR-35 |
| `tests/orchestrator/test_sentinels_fanout.py` | create | parser unit tests (offline) |
| `tests/orchestrator/test_lifecycle_child_worktree.py` | create | `provision_workspace` child-branching (real tmp git repo) |
| `tests/orchestrator/test_fanout_loop.py` | create | loop-level `fanout` signal → `awaiting_children` |
| `tests/orchestrator/test_fanout_dispatch.py` | create | `_dispatch_children` + depth-bound unit tests |
| `tests/orchestrator/test_fanout_integration.py` | create | scripted-harness end-to-end: parent fanout, 2 children run to done |

## ADR claim

**ADR-35** is needed. Decision: fanout concurrency cap via `asyncio.Semaphore` in the supervisor (Option A). Load-bearing because it determines whether child run rows survive a restart and how the semaphore integrates with the supervisor task. Alternative (Option B, queue-and-block) rejected for requiring new persistent state.

## Open contract questions (resolve before Task 1)

These were flagged during plan review and are **not yet locked**. Resolve each one before writing implementation code — they shape APIs that later tasks (and 9c) depend on. If a decision feels obvious, write the one-line rationale into the plan inline (or into ADR-35 if load-bearing) so future readers can see why.

### OCQ-1 — Channel for `join_prompt` to flow from 9b → 9c

The plan currently stashes the full fanout payload (children + `join_prompt`) in `iters.signal_args["payload"]` of the closing fanout iter. 9c reads `signal_args["payload"]["join_prompt"]` from there when it builds the synthesizer iter.

**Alternatives to weigh before committing:**

- **(a) Status quo — `iters.signal_args["payload"]`.** Zero new schema. But `signal_args` is opaque-ish across the codebase and the dependency between 9b's write and 9c's read is implicit (only enforced by a comment + a test). Cheap, slightly leaky.
- **(b) Dedicated column on `iters` (e.g. `fanout_payload JSON`).** Explicit, queryable, type-able via a Pydantic model on read. Requires a schema bump (hand-rolled `create_all` per ADR-17 — but a column add at this stage is still cheap since we're pre-V1). Tighter contract, more code.
- **(c) Separate `fanout_dispatch` event row carrying the payload as `data`.** Event-store-native (ADR-10), naturally orderable, no schema change beyond extending taxonomy. But it duplicates info already in `subagent_dispatch` events and forces 9c to query events instead of the closing iter row.

**Recommendation:** (a) for the MVP, with a comment in `_apply_result` and a dedicated test (`test_fanout_loop.py::test_closing_iter_signal_args_contains_payload`) that asserts the exact shape 9c expects. Re-evaluate when 9c lands; promoting to (b) is a one-task migration if (a) feels too implicit. Confirm before Task 7.

### OCQ-2 — Orphan-via-children edge case (interaction with 9a sweep)

A parent in `awaiting_children` whose children all get swept as orphans on restart (ADR-34 cascade) will reach a state where every child has a `run_ended` event but the parent has no live signal to resume. The 9a `_cascade_cancel_descendants` helper finalises the parent **together with** its descendants when it walks the tree from the parent — so this path is mostly covered. But:

- The cascade helper triggers when `_recover_orphans` finds the parent in `awaiting_children` at startup. If the parent is *already past startup* and children are swept by a *future* restart, the parent row is still `awaiting_children` and the cascade re-runs correctly on that future restart. OK.
- The path where children settle to terminal *without* a restart (the normal 9b path) is handled by 9c's join logic — explicitly out-of-scope here.
- The path where the parent is `awaiting_children` and a single child crashes mid-flight (parent process alive, one child task dies) — 9c needs a child-completion watcher that fires on *any* terminal event. 9b just has to ensure the child row is written and the parent state is set; do not add resume logic in 9b.

**Action for 9b implementer:** Add one test to `test_fanout_integration.py` that simulates "restart with parent in `awaiting_children`": create parent + 2 children rows, call `_recover_orphans` directly, assert the cascade finalises all three with the right summary. This guards the seam between 9a's sweep and 9b's dispatch shape without overreaching into 9c.

## Tasks (TDD-ordered)

---

### Task 1 — Fanout payload model

**~10 min**

- [ ] Create `src/relay_v2/harness/signaling/fanout.py`:

```python
"""Pydantic models for the fanout sentinel payload (spec.md §5.1 / §12, 9b).

``FanoutPayload`` validates the JSON body between
``[[engteam:fanout-start]]`` and ``[[engteam:fanout-end]]``.
``FanoutParseError`` is raised when JSON fails to parse or the payload
fails validation; the orchestrator treats it identically to
:class:`~relay_v2.harness.signaling.MarkerError`.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

__all__ = ["FanoutChild", "FanoutParseError", "FanoutPayload"]


class FanoutParseError(Exception):
    """Raised when the fanout JSON body fails to parse or validate."""


class FanoutChild(BaseModel):
    role: str
    prompt: str


class FanoutPayload(BaseModel):
    children: list[FanoutChild]
    join_prompt: str

    @field_validator("children")
    @classmethod
    def at_least_one(cls, v: list[FanoutChild]) -> list[FanoutChild]:
        if not v:
            raise ValueError("fanout payload must list at least one child")
        return v
```

- [ ] Create `tests/orchestrator/test_sentinels_fanout.py`:

```python
"""Unit tests for the fanout sentinel grammar (9b). All offline — no pi."""
from __future__ import annotations

import pytest
from relay_v2.harness.signaling.fanout import FanoutParseError, FanoutPayload


def test_fanout_payload_valid() -> None:
    p = FanoutPayload.model_validate({
        "children": [
            {"role": "explorer-a", "prompt": "Do A."},
            {"role": "explorer-b", "prompt": "Do B."},
        ],
        "join_prompt": "Synthesize A and B.",
    })
    assert len(p.children) == 2
    assert p.children[0].role == "explorer-a"
    assert p.join_prompt == "Synthesize A and B."


def test_fanout_payload_empty_children_raises() -> None:
    with pytest.raises(Exception):
        FanoutPayload.model_validate({"children": [], "join_prompt": "x"})


def test_fanout_payload_missing_join_prompt_raises() -> None:
    with pytest.raises(Exception):
        FanoutPayload.model_validate({
            "children": [{"role": "r", "prompt": "p"}],
        })
```

- [ ] `uv run pytest tests/orchestrator/test_sentinels_fanout.py -x` — 3 pass.
- [ ] `uv run mypy src/relay_v2/harness/signaling/fanout.py` — clean.

**Commit:** `feat(sentinels): FanoutPayload + FanoutParseError model (9b)`

---

### Task 2 — Sentinel regex + `count_closing_sentinels` extension

**~10 min**

- [ ] In `src/relay_v2/harness/signaling/sentinels.py` after line 46 (`_BLANK_RE`), add:

```python
_FANOUT_RE = re.compile(r"^\[\[engteam:fanout\]\][ \t]*$")
_FANOUT_START_RE = re.compile(r"^\[\[engteam:fanout-start\]\][ \t]*$")
_FANOUT_END_RE = re.compile(r"^\[\[engteam:fanout-end\]\][ \t]*$")
```

- [ ] Replace `count_closing_sentinels` body (lines 89–101) — add `fanout` counter and match:

```python
def count_closing_sentinels(text: str) -> dict[str, int]:
    """Count line-anchored closing sentinels."""
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
```

- [ ] Add to `tests/orchestrator/test_sentinels_fanout.py`:

```python
from relay_v2.harness.signaling.sentinels import count_closing_sentinels


def test_count_fanout_sentinel() -> None:
    counts = count_closing_sentinels("Some work.\n\n[[engteam:fanout]]\n")
    assert counts["fanout"] == 1 and counts["done"] == 0


def test_count_fanout_not_at_column_zero_ignored() -> None:
    assert count_closing_sentinels("    [[engteam:fanout]]\n")["fanout"] == 0


def test_count_existing_sentinels_unaffected() -> None:
    counts = count_closing_sentinels("All done.\n\n[[engteam:done]]")
    assert counts["done"] == 1 and counts["fanout"] == 0
```

- [ ] `uv run pytest tests/orchestrator/test_sentinels_fanout.py -x` — 6 pass.

**Commit:** `feat(sentinels): fanout regex + count_closing_sentinels fanout key (9b)`

---

### Task 3 — `extract_fanout_payload` + `__init__` re-exports

**~25 min**

- [ ] Add to `sentinels.py` after `extract_phase_start`, before `_first_attr`. Also add `"extract_fanout_payload"` to `__all__`:

```python
def extract_fanout_payload(text: str) -> "FanoutPayload":  # noqa: F821
    """Extract and validate the JSON between ``[[engteam:fanout-start]]``
    and ``[[engteam:fanout-end]]`` in the turn containing ``[[engteam:fanout]]``.

    Raises :class:`MarkerError` when the block is structurally absent.
    Raises :class:`~relay_v2.harness.signaling.fanout.FanoutParseError`
    on invalid JSON or a payload that fails ``FanoutPayload`` validation.
    """
    import json as _json

    from pydantic import ValidationError

    from relay_v2.harness.signaling.fanout import FanoutParseError, FanoutPayload

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
```

- [ ] Update `src/relay_v2/harness/signaling/__init__.py`:

```python
"""Signaling strategies (ADR-05)."""

from relay_v2.harness.protocol import SignalConfig, SignalEmitted
from relay_v2.harness.signaling.fanout import (
    FanoutChild,
    FanoutParseError,
    FanoutPayload,
)
from relay_v2.harness.signaling.sentinels import (
    MarkerError,
    count_closing_sentinels,
    detect_in_text,
    extract_fanout_payload,
    extract_handoff_prompt,
    extract_pause_id,
    extract_pause_prompt,
    extract_pause_question,
    extract_phase_start,
    validate_done_no_prompt_markers,
)

__all__ = [
    "FanoutChild",
    "FanoutParseError",
    "FanoutPayload",
    "MarkerError",
    "SignalConfig",
    "SignalEmitted",
    "count_closing_sentinels",
    "detect_in_text",
    "extract_fanout_payload",
    "extract_handoff_prompt",
    "extract_pause_id",
    "extract_pause_prompt",
    "extract_pause_question",
    "extract_phase_start",
    "validate_done_no_prompt_markers",
]
```

- [ ] Add extraction tests to `tests/orchestrator/test_sentinels_fanout.py`:

```python
from relay_v2.harness.signaling.sentinels import extract_fanout_payload
from relay_v2.harness.signaling.fanout import FanoutParseError
from relay_v2.harness.signaling import MarkerError

FANOUT_BLOCK = (
    "Dispatching parallel exploration.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "explorer-a", "prompt": "Do A."}, '
    '{"role": "explorer-b", "prompt": "Do B."}], "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)


def test_extract_fanout_payload_valid() -> None:
    payload = extract_fanout_payload(FANOUT_BLOCK)
    assert len(payload.children) == 2
    assert payload.children[0].role == "explorer-a"
    assert payload.children[1].prompt == "Do B."
    assert payload.join_prompt == "Merge."


def test_extract_fanout_payload_no_end_marker_raises_marker_error() -> None:
    with pytest.raises(MarkerError):
        extract_fanout_payload(
            "[[engteam:fanout-start]]\n{}\n[[engteam:fanout]]"
        )


def test_extract_fanout_payload_no_start_marker_raises_marker_error() -> None:
    with pytest.raises(MarkerError):
        extract_fanout_payload(
            '{"x": 1}\n[[engteam:fanout-end]]\n\n[[engteam:fanout]]'
        )


def test_extract_fanout_payload_bad_json_raises_parse_error() -> None:
    with pytest.raises(FanoutParseError):
        extract_fanout_payload(
            "[[engteam:fanout-start]]\n{not valid}\n"
            "[[engteam:fanout-end]]\n\n[[engteam:fanout]]"
        )


def test_extract_fanout_payload_empty_children_raises_parse_error() -> None:
    with pytest.raises(FanoutParseError):
        extract_fanout_payload(
            "[[engteam:fanout-start]]\n"
            '{"children": [], "join_prompt": "x"}\n'
            "[[engteam:fanout-end]]\n\n[[engteam:fanout]]"
        )


def test_extract_fanout_payload_multiline_json() -> None:
    text = (
        "[[engteam:fanout-start]]\n"
        "{\n"
        '  "children": [{"role": "r", "prompt": "p"}],\n'
        '  "join_prompt": "j"\n'
        "}\n"
        "[[engteam:fanout-end]]\n\n"
        "[[engteam:fanout]]"
    )
    assert extract_fanout_payload(text).children[0].role == "r"
```

- [ ] `uv run pytest tests/orchestrator/test_sentinels_fanout.py -x` — all 12 pass.
- [ ] `uv run mypy src/relay_v2/harness/signaling/` — clean.

**Commit:** `feat(sentinels): extract_fanout_payload + __init__ re-exports (9b)`

---

### Task 4 — `detect_in_text` fanout branch

**~10 min**

- [ ] In `sentinels.py` `detect_in_text`, add the fanout check immediately after the `pause` branch (before the non-terminal `unit_*`/`phase_start` checks):

```python
    if counts.get("fanout"):
        # FanoutParseError and MarkerError propagate to the loop's
        # _drive_iter catch clause (loop.py — Task 6).
        payload = extract_fanout_payload(text)
        return SignalEmitted(
            kind="fanout",
            args={"payload": payload.model_dump()},
        )
```

- [ ] Add detect tests to `tests/orchestrator/test_sentinels_fanout.py`:

```python
from relay_v2.harness.protocol import SignalConfig
from relay_v2.harness.signaling.sentinels import detect_in_text

_CFG = SignalConfig(strategy="text_sentinels")


def test_detect_in_text_fanout_returns_fanout_signal() -> None:
    sig = detect_in_text(FANOUT_BLOCK, _CFG)
    assert sig is not None
    assert sig.kind == "fanout"
    assert sig.args["payload"]["join_prompt"] == "Merge."
    assert sig.args["payload"]["children"][0]["role"] == "explorer-a"


def test_detect_in_text_fanout_beats_unit_done() -> None:
    """fanout in same text as unit_done: fanout wins (terminal beats non-terminal)."""
    text = FANOUT_BLOCK + '\n\n[[engteam:unit-done id="u1" title="s"]]\n'
    sig = detect_in_text(text, _CFG)
    assert sig is not None and sig.kind == "fanout"


def test_detect_in_text_no_fanout_sentinel_returns_none() -> None:
    assert detect_in_text("Ordinary text.", _CFG) is None
```

- [ ] `uv run pytest tests/orchestrator/test_sentinels_fanout.py -x` — all 15 pass.
- [ ] `uv run ruff check src/relay_v2/harness/` — clean.

**Commit:** `feat(sentinels): detect_in_text handles fanout closing verb (9b)`

---

### Task 5 — Config additions

**~5 min**

- [ ] In `src/relay_v2/config.py` after the `iter_timeout` field, add:

```python
    # Fanout/join (Phase 9, ADR-35).
    # max_fanout_depth: maximum parent→child recursion depth.
    #   Default 2, hard cap 4 (proposal §recursion-bounds).
    # max_fanout_concurrent: semaphore pool size for concurrent child-run
    #   tasks across all active parents (Option A, ADR-35).
    max_fanout_depth: int = 2
    max_fanout_concurrent: int = 4
```

- [ ] `uv run mypy src/relay_v2/config.py` — clean.
- [ ] `uv run pytest` — full suite ~211 pass.

**Commit:** `feat(config): max_fanout_depth + max_fanout_concurrent (9b)`

---

### Task 6 — `LoopResult` extension + loop `fanout` branch

**~20 min**

- [ ] In `src/relay_v2/orchestrator/loop.py`:

  **6a.** Add import (top of file, after existing imports):
  ```python
  from relay_v2.harness.signaling.fanout import FanoutParseError
  ```

  **6b.** Extend `_TERMINAL` (line 57):
  ```python
  _TERMINAL = {"done", "handoff", "pause", "fanout"}
  ```

  **6c.** Add `fanout_payload` to `LoopResult` after `pause_id`:
  ```python
      fanout_payload: dict | None = None
  ```

  **6d.** In `_drive_iter`, change the `except MarkerError` clause (around line 163) to also catch `FanoutParseError`:
  ```python
                  except (MarkerError, FanoutParseError) as err:
                      out.marker_headline = (
                          err.headline
                          if isinstance(err, MarkerError)
                          else str(err)
                      )
                      break
  ```

  **6e.** In `run_loop`, after the `pause` return (after line 370), add:
  ```python
              if signal.kind == "fanout":
                  return LoopResult(
                      "awaiting_children",
                      reason="signal",
                      fanout_payload=signal.args.get("payload"),
                  )
  ```

- [ ] Create `tests/orchestrator/test_fanout_loop.py`:

```python
"""Loop-level fanout signal tests (9b). Scripted harness, no pi."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Iter, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

FANOUT_BLOCK = (
    "Dispatching.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "a", "prompt": "A."}, {"role": "b", "prompt": "B."}],'
    ' "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
DONE = "Done.\n\n[[engteam:done]]"


def _settings(tmp_path: Path) -> Settings:
    return Settings(data_dir=tmp_path / ".relay")


def _run_sync(coro, settings, harness):
    async def _main():
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()
    return asyncio.run(_main())


def test_fanout_signal_transitions_parent_to_awaiting_children(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    # parent + 2 children
    harness = ScriptedHarness(
        [TextScript(FANOUT_BLOCK), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "awaiting_children"
        assert result.fanout_payload is not None
        assert len(result.fanout_payload["children"]) == 2
        return run_id

    run_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            run = s.get(Run, run_id)
            assert run is not None
            assert run.status == "awaiting_children"
            assert run.ended_at is None
            closing_iter = s.scalar(
                select(Iter)
                .where(Iter.run_id == run_id)
                .order_by(Iter.seq.desc())
                .limit(1)
            )
            assert closing_iter is not None
            assert closing_iter.signal_kind == "fanout"
            assert closing_iter.exit_reason == "signal"
    finally:
        engine.dispose()


def test_fanout_bad_json_fails_run(tmp_path: Path) -> None:
    """Malformed fanout JSON propagates as FanoutParseError → run fails."""
    bad_fanout = (
        "[[engteam:fanout-start]]\n"
        "{not valid json}\n"
        "[[engteam:fanout-end]]\n\n"
        "[[engteam:fanout]]"
    )
    settings = _settings(tmp_path)
    harness = ScriptedHarness([TextScript(bad_fanout)])

    async def scenario(core: RelayCore) -> None:
        pid = await core.register_project(tmp_path, "p")
        run_id = await core.start_run(pid, "Go.")
        result = await core.wait_for_run(run_id)
        assert result.status == "failed"

    _run_sync(scenario, settings, harness)
```

- [ ] `uv run pytest tests/orchestrator/test_fanout_loop.py::test_fanout_bad_json_fails_run -x` — should pass immediately (FanoutParseError flows through the MarkerError path). The `awaiting_children` test fails (missing `_dispatch_children`). Record; proceed to Task 7.
- [ ] `uv run mypy src/relay_v2/orchestrator/loop.py` — clean.

**Commit:** `feat(loop): fanout branch + FanoutParseError catch (9b)`

---

### Task 7 — `lifecycle.py` extensions

**~30 min**

Three changes to `src/relay_v2/orchestrator/lifecycle.py`:

**7a.** Add `parent_run_id: str | None = None` field to `RunContext` dataclass, after `body`:
```python
    parent_run_id: str | None = None  # set for child runs dispatched via fanout (9b)
```

**7b.** Extend `create_run` signature with `parent_run_id: str | None = None` and thread it into the `Run(...)` constructor:
```python
async def create_run(
    sm: async_sessionmaker[AsyncSession],
    *,
    run_id: str,
    project_id: int,
    prompt_body: str,
    max_iters: int,
    iter_timeout: int,
    worktree_path: str | None,
    branch: str | None,
    parent_run_id: str | None = None,
) -> None:
    async with sm() as s:
        s.add(
            Run(
                id=run_id,
                project_id=project_id,
                prompt_body=prompt_body,
                status="running",
                max_iters=max_iters,
                iter_timeout=iter_timeout,
                worktree_path=worktree_path,
                branch=branch,
                parent_run_id=parent_run_id,
            )
        )
        await s.commit()
```

**7c.** Extend `provision_workspace` with `parent_worktree_path: Path | None = None`. When the parent worktree exists, resolve its HEAD commit SHA and use it as the start-point for `git worktree add`:

```python
async def provision_workspace(
    project_root: Path,
    data_dir: Path,
    run_id: str,
    parent_worktree_path: Path | None = None,
) -> tuple[Path | None, str | None, Path]:
    """Create the artifacts dir; best-effort per-run git worktree.

    When ``parent_worktree_path`` is given and exists, branches the new
    worktree off the parent worktree's HEAD commit rather than the
    project default branch (spec.md §6 — child runs start from the
    parent's in-progress work). When the parent worktree path does not
    exist or git fails, degrades to branching from the project HEAD.
    """
    run_dir = data_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    worktrees = data_dir / "worktrees"
    wt = worktrees / run_id
    branch = f"relay/{run_id}"
    worktrees.mkdir(parents=True, exist_ok=True)

    # Resolve parent HEAD commit as start-point (child branches from
    # parent's in-progress state, not the project default branch tip).
    parent_commit: str | None = None
    if parent_worktree_path is not None and parent_worktree_path.is_dir():
        head_proc = await _spawn_argv(
            "git", "-C", str(parent_worktree_path),
            "rev-parse", "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await head_proc.communicate()
        if head_proc.returncode == 0:
            parent_commit = stdout.decode().strip()

    git_cmd = [
        "git", "-C", str(project_root),
        "worktree", "add", "-b", branch, str(wt),
    ]
    if parent_commit:
        git_cmd.append(parent_commit)

    proc = await _spawn_argv(
        *git_cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    rc = await proc.wait()
    if rc == 0 and wt.exists():
        return wt, branch, run_dir
    return None, None, run_dir
```

- [ ] Create `tests/orchestrator/test_lifecycle_child_worktree.py`:

```python
"""provision_workspace branches child worktree off parent HEAD (9b)."""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from relay_v2.orchestrator.lifecycle import provision_workspace


async def _git(*args: str, cwd: Path) -> int:
    proc = await asyncio.create_subprocess_exec(
        "git", *args, cwd=str(cwd),
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    return await proc.wait()


async def _setup_repo(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    await _git("init", cwd=path)
    await _git("config", "user.email", "t@t.com", cwd=path)
    await _git("config", "user.name", "T", cwd=path)
    (path / "README.md").write_text("init")
    await _git("add", ".", cwd=path)
    await _git("commit", "-m", "init", cwd=path)


@pytest.mark.asyncio
async def test_child_worktree_contains_parent_commits(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    await _setup_repo(project)
    data_dir = tmp_path / ".relay"

    parent_wt, _, _ = await provision_workspace(project, data_dir, "parent-001")
    assert parent_wt is not None

    # Commit work in the parent worktree.
    (parent_wt / "work.txt").write_text("parent work")
    await _git("add", ".", cwd=parent_wt)
    await _git("commit", "-m", "parent progress", cwd=parent_wt)

    # Child branches off parent HEAD.
    child_wt, child_branch, _ = await provision_workspace(
        project, data_dir, "child-001",
        parent_worktree_path=parent_wt,
    )
    assert child_wt is not None
    assert (child_wt / "work.txt").exists()
    assert (child_wt / "work.txt").read_text() == "parent work"


@pytest.mark.asyncio
async def test_child_worktree_missing_parent_degrades_gracefully(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    await _setup_repo(project)
    data_dir = tmp_path / ".relay"
    missing = tmp_path / "nonexistent"

    wt, branch, run_dir = await provision_workspace(
        project, data_dir, "child-002",
        parent_worktree_path=missing,
    )
    assert wt is not None  # still succeeds, branches from project HEAD
    assert run_dir.exists()


@pytest.mark.asyncio
async def test_provision_workspace_no_parent_unchanged(
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    await _setup_repo(project)
    data_dir = tmp_path / ".relay"

    wt, branch, run_dir = await provision_workspace(project, data_dir, "run-001")
    assert wt is not None
    assert branch == "relay/run-001"
    assert run_dir.exists()
```

- [ ] `uv run pytest tests/orchestrator/test_lifecycle_child_worktree.py -x` — all 3 pass.
- [ ] `uv run mypy src/relay_v2/orchestrator/lifecycle.py` — clean.

**Commit:** `feat(lifecycle): provision_workspace child HEAD branching + create_run parent_run_id (9b)`

---

### Task 8 — `RelayCore` semaphore + depth helper + `_dispatch_children` + `_apply_result`

**~50 min**

All changes in `src/relay_v2/core.py`.

**8a.** Add semaphore field to `__init__` (after `_enqueue_lock`):
```python
        # Fanout concurrency cap (ADR-35, 9b). Initialized in start()
        # after the event loop exists; None before then.
        self._fanout_sem: asyncio.Semaphore | None = None
```

**8b.** Initialize in `start()` after `bootstrap_engine.dispose()`:
```python
        self._fanout_sem = asyncio.Semaphore(
            self._settings.max_fanout_concurrent
        )
```

**8c.** Gate child tasks in `_supervise` — replace the current body with:
```python
    async def _supervise(self) -> None:
        while True:
            ctx = await self._queue.get()
            if ctx.parent_run_id is not None and self._fanout_sem is not None:
                # Child run: acquire slot before creating the task so at
                # most max_fanout_concurrent children run concurrently.
                # The done-callback releases regardless of outcome.
                await self._fanout_sem.acquire()
                task = asyncio.create_task(self._run(ctx))
                sem = self._fanout_sem

                def _release(t: asyncio.Task[None], s: asyncio.Semaphore = sem) -> None:
                    s.release()

                task.add_done_callback(_release)
            else:
                task = asyncio.create_task(self._run(ctx))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
            self._queue.task_done()
```

**8d.** Add `_fanout_depth` helper after `_cascade_cancel_descendants`:
```python
    async def _fanout_depth(self, run_id: str) -> int:
        """Walk the parent_run_id chain and return the depth (0 = root).

        Bounded by ``max_fanout_depth + 1`` hops to guard against a
        malformed DB cycle.
        """
        depth = 0
        current_id: str | None = run_id
        cap = self._settings.max_fanout_depth + 2
        while current_id is not None and depth <= cap:
            async with self._sm() as s:
                row = await s.get(Run, current_id)
            if row is None:
                break
            current_id = row.parent_run_id
            if current_id is not None:
                depth += 1
        return depth
```

**8e.** Add `_dispatch_children` after `_fanout_depth`:
```python
    async def _dispatch_children(
        self,
        parent_run_id: str,
        parent_worktree_path: Path | None,
        fanout_payload: dict,
        iter_id: int | None,
    ) -> None:
        """Create N child runs and enqueue them (spec.md §6, 9b).

        Depth bound (ADR-35): raises ``ValueError`` when
        ``depth(parent) + 1 > max_fanout_depth``.
        """
        from relay_v2.harness.signaling.fanout import FanoutPayload

        parent_depth = await self._fanout_depth(parent_run_id)
        if parent_depth + 1 > self._settings.max_fanout_depth:
            raise ValueError(
                f"fanout depth limit: parent {parent_run_id} is at depth "
                f"{parent_depth}, max_fanout_depth="
                f"{self._settings.max_fanout_depth}"
            )

        payload = FanoutPayload.model_validate(fanout_payload)

        async with self._sm() as s:
            parent_run = await s.get(Run, parent_run_id)
            if parent_run is None:
                raise ValueError(f"parent run {parent_run_id} not found")
            project_id = parent_run.project_id

        async with self._sm() as s:
            project = await s.get(Project, project_id)
            if project is None:
                raise ValueError(f"project {project_id} not found")
            project_root = Path(project.root_path)

        for child in payload.children:
            child_run_id = self._new_run_id()
            wt, branch, run_dir = await provision_workspace(
                project_root,
                self._settings.data_dir,
                child_run_id,
                parent_worktree_path=parent_worktree_path,
            )
            await create_run(
                self._sm,
                run_id=child_run_id,
                project_id=project_id,
                prompt_body=child.prompt,
                max_iters=self._settings.max_iters,
                iter_timeout=self._settings.iter_timeout,
                worktree_path=str(wt) if wt else None,
                branch=branch,
                parent_run_id=parent_run_id,
            )
            # subagent_dispatch on the parent stream (spec.md §3.2).
            await self._store.append(
                parent_run_id,
                "subagent_dispatch",
                {
                    "child_run_id": child_run_id,
                    "role": child.role,
                    "prompt": child.prompt,
                },
                iter_id=iter_id,
            )
            # run_started on the child's own stream.
            await self._store.append(
                child_run_id,
                "run_started",
                {
                    "project_id": project_id,
                    "prompt_body": child.prompt,
                    "max_iters": self._settings.max_iters,
                },
            )
            self._runs[child_run_id] = _RunState()
            await self._queue.put(
                RunContext(
                    run_id=child_run_id,
                    project_root=project_root,
                    worktree_path=wt,
                    run_dir=run_dir,
                    max_iters=self._settings.max_iters,
                    iter_timeout=self._settings.iter_timeout,
                    start_seq=0,
                    phase=None,
                    body=child.prompt,
                    parent_run_id=parent_run_id,
                )
            )
```

**8f.** Add `awaiting_children` branch to `_apply_result`, before the existing terminal handling:
```python
    async def _apply_result(
        self, ctx: RunContext, result: LoopResult
    ) -> None:
        if result.status == "paused":
            await set_run_status(
                self._sm, ctx.run_id, "paused", ended=False
            )
            await self._store.append(
                ctx.run_id, "pause_requested",
                {"question": result.question or ""},
            )
            return
        if result.status == "awaiting_children":
            # Status first so SSE consumers see a consistent state when
            # the subagent_dispatch events land.
            await set_run_status(
                self._sm, ctx.run_id, "awaiting_children", ended=False
            )
            # Find the closing iter's id for iter-scoped dispatch events.
            async with self._sm() as s:
                closing = await s.scalar(
                    select(Iter)
                    .where(Iter.run_id == ctx.run_id)
                    .order_by(Iter.seq.desc())
                    .limit(1)
                )
            await self._dispatch_children(
                parent_run_id=ctx.run_id,
                parent_worktree_path=ctx.worktree_path,
                fanout_payload=result.fanout_payload or {},
                iter_id=closing.id if closing else None,
            )
            return
        await set_run_status(
            self._sm, ctx.run_id, result.status, ended=True
        )
        await self._store.append(
            ctx.run_id,
            "run_ended",
            {"status": result.status,
             "summary": result.summary or result.reason},
        )
```

Add `from sqlalchemy import select` and `from relay_v2.db.models import Iter` imports to `core.py` if not already present. (Iter is already imported via `from relay_v2.db.models import Event, Iter, Project, Prompt, Run`; `select` is already imported via `from sqlalchemy import func, select`.)

**8g.** Extend `start_run` — add `parent_run_id: str | None = None` parameter and thread it through `create_run` and `RunContext`:

```python
    async def start_run(
        self,
        project_id: int,
        prompt_body: str,
        *,
        max_iters: int | None = None,
        iter_timeout: int | None = None,
        parent_run_id: str | None = None,
    ) -> str:
```

In the `create_run` call, add `parent_run_id=parent_run_id`.
In the `RunContext(...)` constructor, add `parent_run_id=parent_run_id`.

- [ ] `uv run mypy src/relay_v2/core.py` — clean.

**Commit:** `feat(core): _dispatch_children + semaphore + _fanout_depth + start_run parent_run_id (9b)`

---

### Task 9 — `_dispatch_children` unit tests

**~30 min**

Create `tests/orchestrator/test_fanout_dispatch.py`:

```python
"""Unit tests for RelayCore._dispatch_children and depth enforcement (9b)."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Event, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

DONE = "Done.\n\n[[engteam:done]]"
FANOUT_TWO = (
    "Dispatching.\n\n"
    "[[engteam:fanout-start]]\n"
    '{"children": [{"role": "a", "prompt": "Do A."}, '
    '{"role": "b", "prompt": "Do B."}], "join_prompt": "Merge."}\n'
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)


def _settings(tmp_path: Path, **kw) -> Settings:
    return Settings(data_dir=tmp_path / ".relay", **kw)


def _run_sync(coro, settings, harness):
    async def _main():
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            return await coro(core)
        finally:
            await core.aclose()
    return asyncio.run(_main())


def test_dispatch_creates_two_child_runs_with_parent_run_id(
    tmp_path: Path,
) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> tuple[str, list[str]]:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        engine = create_engine(settings.db_url)
        try:
            with Session(engine) as s:
                children = list(
                    s.scalars(select(Run).where(Run.parent_run_id == parent_id))
                )
        finally:
            engine.dispose()
        child_ids = [c.id for c in children]
        for cid in child_ids:
            await core.wait_for_run(cid)
        return parent_id, child_ids

    parent_id, child_ids = _run_sync(scenario, settings, harness)

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            parent = s.get(Run, parent_id)
            assert parent is not None and parent.status == "awaiting_children"
            assert parent.ended_at is None
            assert len(child_ids) == 2
            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.parent_run_id == parent_id
                assert child.status == "done"
            dispatches = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "subagent_dispatch",
                    )
                )
            )
            assert len(dispatches) == 2
            roles = {e.payload["role"] for e in dispatches}
            assert roles == {"a", "b"}
    finally:
        engine.dispose()


def test_subagent_dispatch_events_are_iter_scoped(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        return parent_id

    parent_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            dispatches = list(
                s.scalars(
                    select(Event).where(
                        Event.run_id == parent_id,
                        Event.kind == "subagent_dispatch",
                    )
                )
            )
            assert all(e.iter_id is not None for e in dispatches)
    finally:
        engine.dispose()


def test_dispatch_depth_limit_fails_child_run(tmp_path: Path) -> None:
    """Child at depth 1 trying to fanout when max_fanout_depth=1 fails."""
    settings = _settings(tmp_path, max_fanout_depth=1)
    # parent fanouts → child-a tries to fanout (exceeds cap) → child-b done
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(FANOUT_TWO), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> tuple[str, list[str]]:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        engine = create_engine(settings.db_url)
        try:
            with Session(engine) as s:
                children = list(
                    s.scalars(select(Run).where(Run.parent_run_id == parent_id))
                )
        finally:
            engine.dispose()
        child_ids = [c.id for c in children]
        for cid in child_ids:
            await core.wait_for_run(cid)
        return parent_id, child_ids

    parent_id, child_ids = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            statuses = {s.get(Run, cid).status for cid in child_ids}
            assert "failed" in statuses  # depth-exceeded child fails
            assert "done" in statuses    # other child succeeds
    finally:
        engine.dispose()


def test_parent_run_no_run_ended_event(tmp_path: Path) -> None:
    """awaiting_children parent must have no run_ended event (9c's territory)."""
    settings = _settings(tmp_path)
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def scenario(core: RelayCore) -> str:
        pid = await core.register_project(tmp_path, "p")
        parent_id = await core.start_run(pid, "Start.")
        await core.wait_for_run(parent_id)
        return parent_id

    parent_id = _run_sync(scenario, settings, harness)
    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            kinds = [
                e.kind for e in s.scalars(
                    select(Event).where(Event.run_id == parent_id).order_by(Event.seq)
                )
            ]
            assert "run_ended" not in kinds
    finally:
        engine.dispose()
```

- [ ] `uv run pytest tests/orchestrator/test_fanout_dispatch.py -x` — all 4 pass.
- [ ] `uv run pytest tests/orchestrator/test_fanout_loop.py -x` — now all pass (Task 6 test unblocked by Task 8).
- [ ] `uv run ruff check .` — clean.
- [ ] `uv run mypy src/relay_v2/` — clean.

**Commit:** `test(core): dispatch_children creates children + depth bound enforcement (9b)`

---

### Task 10 — Integration test

**~30 min**

Create `tests/orchestrator/test_fanout_integration.py`:

```python
"""Phase 9b integration test — scripted fanout-to-2-children.

Scenario: parent iter emits [[engteam:fanout]] with 2 children.
Both children execute independently and reach done.
Parent stays in awaiting_children (no join — that is 9c).

Acceptance criteria (proposal §9b acceptance):
- Two Run rows with parent_run_id set.
- Two subagent_dispatch events recorded on the parent, iter-scoped.
- Parent status awaiting_children, ended_at NULL, no run_ended event.
- Each child has run_started, iter_started, run_ended in sequence.
- Closing parent iter: signal_kind=fanout, exit_reason=signal.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from relay_v2.config import Settings
from relay_v2.core import RelayCore
from relay_v2.db.models import Event, Iter, Run
from tests.orchestrator.scripted_harness import ScriptedHarness, TextScript

FANOUT_TWO = (
    "Dispatching two explorers.\n\n"
    "[[engteam:fanout-start]]\n"
    "{"
    '"children": ['
    '{"role": "explorer-frontend", "prompt": "Audit frontend."},'
    '{"role": "explorer-backend", "prompt": "Audit backend."}'
    '],'
    '"join_prompt": "Synthesize the two audits."'
    "}\n"
    "[[engteam:fanout-end]]\n\n"
    "[[engteam:fanout]]"
)
DONE = "Audit complete.\n\n[[engteam:done]]"


def test_fanout_to_two_children_full_scenario(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path / ".relay")
    harness = ScriptedHarness(
        [TextScript(FANOUT_TWO), TextScript(DONE), TextScript(DONE)]
    )

    async def _run() -> dict:
        core = RelayCore(settings, harness=harness)
        await core.start()
        try:
            pid = await core.register_project(tmp_path, "p")
            parent_id = await core.start_run(pid, "Investigate the system.")
            parent_result = await core.wait_for_run(parent_id)
            assert parent_result.status == "awaiting_children", (
                f"expected awaiting_children, got {parent_result.status}"
            )
            engine = create_engine(settings.db_url)
            try:
                with Session(engine) as s:
                    children = list(
                        s.scalars(select(Run).where(Run.parent_run_id == parent_id))
                    )
            finally:
                engine.dispose()
            child_ids = [c.id for c in children]
            assert len(child_ids) == 2
            for cid in child_ids:
                cr = await core.wait_for_run(cid)
                assert cr.status == "done", f"child {cid}: {cr.status}"
            return {"parent_id": parent_id, "child_ids": child_ids}
        finally:
            await core.aclose()

    result = asyncio.run(_run())
    parent_id = result["parent_id"]
    child_ids = result["child_ids"]

    engine = create_engine(settings.db_url)
    try:
        with Session(engine) as s:
            # Parent state
            parent = s.get(Run, parent_id)
            assert parent is not None
            assert parent.status == "awaiting_children"
            assert parent.ended_at is None

            # Parent events
            parent_events = list(
                s.scalars(
                    select(Event).where(Event.run_id == parent_id).order_by(Event.seq)
                )
            )
            parent_kinds = [e.kind for e in parent_events]
            assert parent_kinds[0] == "run_started"
            assert "run_ended" not in parent_kinds
            assert parent_kinds.count("subagent_dispatch") == 2

            dispatches = [e for e in parent_events if e.kind == "subagent_dispatch"]
            assert all(e.iter_id is not None for e in dispatches)
            dispatched_ids = {e.payload["child_run_id"] for e in dispatches}
            assert dispatched_ids == set(child_ids)
            roles = {e.payload["role"] for e in dispatches}
            assert roles == {"explorer-frontend", "explorer-backend"}

            # Closing parent iter
            closing = s.scalar(
                select(Iter)
                .where(Iter.run_id == parent_id)
                .order_by(Iter.seq.desc())
                .limit(1)
            )
            assert closing is not None
            assert closing.signal_kind == "fanout"
            assert closing.exit_reason == "signal"
            assert closing.signal_args is not None
            assert "payload" in closing.signal_args

            # Child state and events
            for cid in child_ids:
                child = s.get(Run, cid)
                assert child is not None
                assert child.parent_run_id == parent_id
                assert child.status == "done"
                assert child.ended_at is not None

                child_kinds = [
                    e.kind for e in s.scalars(
                        select(Event).where(Event.run_id == cid).order_by(Event.seq)
                    )
                ]
                assert child_kinds[0] == "run_started"
                assert child_kinds[-1] == "run_ended"
                assert "iter_started" in child_kinds
    finally:
        engine.dispose()
```

- [ ] `uv run pytest tests/orchestrator/test_fanout_integration.py -x` — passes.
- [ ] `uv run pytest tests/orchestrator/ -x` — all existing tests still pass.

**Commit:** `test(integration): scripted fanout-to-2-children end-to-end (9b)`

---

### Task 11 — Spec + ADR-35 docs update

**~20 min**

**`docs/spec.md` changes:**

1. In §3.1, update the `signal_kind` column comment to add `'fanout'`:
   ```
   signal_kind   TEXT,  -- terminal signal: 'handoff'|'done'|'pause'|'fanout'|NULL
   ```

2. In §5.1, after the signal-kinds list, add:
   ```
   New in 9b: ``fanout`` closes the iter and requests N child runs. The JSON
   payload is carried between ``[[engteam:fanout-start]]`` and
   ``[[engteam:fanout-end]]`` markers; ``[[engteam:fanout]]`` is the closing
   verb. See §12 for the full grammar.
   ```

3. In §12, add a Fanout sentinel subsection:
   ```markdown
   ### Fanout sentinel (9b)

   Closes the iter and requests parallel child runs. Full grammar:

       [[engteam:fanout-start]]
       {
         "children": [
           { "role": "<label>", "prompt": "<child prompt body>" },
           ...
         ],
         "join_prompt": "<prompt body for the synthesizer iter — used in 9c>"
       }
       [[engteam:fanout-end]]

       [[engteam:fanout]]

   The JSON body between ``fanout-start`` and ``fanout-end`` must parse and
   validate as a ``FanoutPayload`` (at least one child; ``join_prompt``
   present). The ``[[engteam:fanout]]`` verb line must follow after the end
   marker (intervening blank lines allowed), at column 0 with no indentation.
   A malformed body, missing markers, or a ``join_prompt``-less payload is
   treated as ``agent_end_no_signal`` and fails the run.

   Depth is limited by ``RELAY_MAX_FANOUT_DEPTH`` (default 2, hard cap 4).
   Concurrent child tasks are bounded by ``RELAY_MAX_FANOUT_CONCURRENT``
   (default 4, Option A semaphore — ADR-35).
   ```

**`docs/decisions.md`** — append ADR-35:

```markdown
## ADR-35 — Fanout concurrency cap: `asyncio.Semaphore` in the supervisor (Option A)

**Status:** Accepted (2026-05-21)
**Phase:** 9b (fanout dispatch)

**Context.** The fanout-join feature dispatches N child runs when a parent iter
emits ``[[engteam:fanout]]``. The proposal names ``max_fanout_concurrent``
(default 4) as an operational guard against too many parallel pi sessions. Two
implementation options were considered:

- **Option A — Semaphore in `RelayCore`:** all N child run rows are created
  immediately as ``running`` rows; the supervisor acquires the semaphore before
  launching each child task and releases on task completion. Children waiting for
  a slot sit in the supervisor queue as ``running`` rows not yet started.
- **Option B — Queue-and-block at dispatch:** `RelayCore` creates only the first N
  rows; the rest are held in a pending queue (in-memory or a new DB table) and
  created/enqueued as slots free.

**Decision.** Option A.

**Rationale.** Option B requires a new persistent queue that must survive server
restart — replicating exactly the gap 9a closed for ``awaiting_children``. An
in-memory queue is lost on restart; a DB queue requires a new table, a new status
value, and a new startup sweep. Option A avoids all of this: every child row
exists in the DB from the moment of dispatch. On restart, the existing
orphan-recovery sweep (ADR-32 / ADR-34) handles them correctly — children waiting
for a semaphore slot are swept as ``running`` orphans, giving them the
"parent interrupted during fanout" summary if their parent is
``awaiting_children``. Fairness across multiple parents is natural: one shared
semaphore pools all concurrent child tasks. The semaphore is created in
``RelayCore.start()`` after the event loop exists, initialized from
``settings.max_fanout_concurrent``.

**Consequences.**
- Child rows created-but-not-yet-executing sit as ``running`` rows in the
  supervisor queue. On restart they are swept as any ``running`` orphan.
- The semaphore count is not persisted across restarts; acceptable for
  single-user MVP (ADR-12).
- ``max_fanout_concurrent`` is a ``Settings`` field
  (``RELAY_MAX_FANOUT_CONCURRENT`` env var, default 4).
- ``max_fanout_depth`` is also a ``Settings`` field
  (``RELAY_MAX_FANOUT_DEPTH``, default 2, hard cap enforced at dispatch time).

**Rejected:** Option B — new persistent intermediate state, new startup sweep,
disproportionate complexity for a single-user MVP guard. An in-memory-only queue
is lost on restart and breaks the restart-recovery guarantee ADR-34 establishes.

**Related:** ADR-12 (single-process MVP), ADR-32 / ADR-34 (orphan recovery),
``docs/proposals/parallel-iters-fanout-join.md``.
```

Also update `docs/spec.md` §11.1 env-var table with:
```
| `RELAY_MAX_FANOUT_DEPTH` | `2` | maximum parent→child recursion depth (hard cap 4) |
| `RELAY_MAX_FANOUT_CONCURRENT` | `4` | concurrent child-run task semaphore pool size |
```

- [ ] `uv run ruff check .` — clean.

**Commit:** `docs(spec,adr): fanout sentinel grammar + ADR-35 concurrency cap (9b)`

---

### Task 12 — Full gate + CLAUDE.md update

**~15 min**

- [ ] `uv run pytest` — expect ~232 passed (211 from 9a + ~21 new), 3 pi-e2e gated.
- [ ] `uv run ruff check .` — clean.
- [ ] `uv run mypy` — clean (strict).
- [ ] `cd frontend && npm run check` — green (142 tests, no frontend changes in 9b).

Update `CLAUDE.md` "Current state" paragraph to record Phase 9b, following the existing format: fanout sentinel parser (`fanout-start/end` marker pair + `fanout` closing verb), `_dispatch_children` with semaphore concurrency cap (ADR-35, Option A), child worktrees branch off parent HEAD, depth bound enforcement, parent transitions to `awaiting_children` and stays there (9c adds join + synthesizer iter), test counts ~232 backend / 142 frontend.

**Commit:** `docs(CLAUDE.md): record Phase 9b under Current state`

---

## Verification commands

```bash
# Per-task verification (run after each task)
uv run pytest tests/orchestrator/test_sentinels_fanout.py -v
uv run pytest tests/orchestrator/test_lifecycle_child_worktree.py -v
uv run pytest tests/orchestrator/test_fanout_loop.py -v
uv run pytest tests/orchestrator/test_fanout_dispatch.py -v
uv run pytest tests/orchestrator/test_fanout_integration.py -v

# Final full gate
uv run pytest                     # ~232 passed, 3 gated
uv run ruff check .
uv run mypy
cd frontend && npm run check      # 142 passed, no changes
```

---

## Out of scope (deferred to 9c)

- **Synthesizer iter**: `join_prompt` is stored in `iters.signal_args["payload"]["join_prompt"]` of the fanout closing iter; 9c reads it there.
- **`child_runs_resolved` event emission**: reserved in taxonomy (9a); 9c emits it.
- **`subagent_return` events**: 9c emits one per child when the completion watcher fires.
- **Parent resume to `running`**: 9c transitions `awaiting_children` → `running` after all children settle.
- **Child-completion watcher**: 9c adds a background task or post-commit hook in `RelayCore` that fires when a child run's `run_ended` event lands and all siblings are terminal.
- **Dashboard "Children" pane**: 9e.
- **OTel span parenting across runs**: 9f.
- **REST `POST /api/runs` exposure of `parent_run_id`**: the internal `start_run(parent_run_id=...)` parameter is not yet exposed over HTTP; used only by `_dispatch_children`. Expose in 9e or on demand.

---

## Risks and what could go wrong

- **`wait_for_run` KeyError on child runs.** `_RunState` entries are registered in `_dispatch_children` before `_queue.put`. Callers of `wait_for_run(child_id)` must wait until after `_dispatch_children` returns (i.e., after `_apply_result` completes for the parent, which is before `state.settled.set()`). The integration test correctly awaits `wait_for_run(parent_id)` first, then iterates children — safe.

- **Semaphore `_release` closure captures wrong semaphore.** The `_supervise` lambda `lambda _t: self._fanout_sem.release()` would fail if `_fanout_sem` is set to `None` during `aclose()`. The task body above captures `sem = self._fanout_sem` as a local default in the callback to avoid this.

- **`provision_workspace` child commit race.** The `git rev-parse HEAD` call on the parent worktree is a read; no lock is held on the parent worktree during the call. On a single-user MVP (ADR-12) this is safe — no concurrent writes to the parent worktree happen between `rev-parse` and `worktree add`.

- **Closing iter `signal_args` must contain `payload`.** `_finish_iter` in `loop.py` calls `close_iter(..., signal_args=signal.args, ...)`. The fanout signal's `args` is `{"payload": {...}}`. The `signal_args` column stores this dict, and 9c reads `signal_args["payload"]["join_prompt"]` from it. Verify the test assertions in `test_fanout_integration.py` include `assert "payload" in closing.signal_args`.

- **`asyncio_mode = "auto"` and sync test functions.** The lifecycle tests use `@pytest.mark.asyncio` (ADR-24: `tests/orchestrator/` uses the `asyncio.run()` wrapper pattern for sync `def test_*`; the lifecycle tests above use `async def` with `@pytest.mark.asyncio`). Under `asyncio_mode = "auto"`, bare `async def test_*` functions run automatically — the marker is redundant but harmless. Consistent with ADR-24.
