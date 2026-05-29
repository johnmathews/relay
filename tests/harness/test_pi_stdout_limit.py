"""Regression: a single pi JSONL line that exceeds asyncio's default
64 KiB ``StreamReader`` buffer must not crash ``PiSession.events()``.

Pi emits one JSON object per line; a large tool result (Read of a big
file, verbose Bash output, ``agent_end.messages`` with long content)
can easily exceed 64 KiB. Without an explicit ``limit=`` on
``create_subprocess_exec``, ``readline()`` raises ``LimitOverrunError``
which surfaces as ``ValueError`` and finalises the run as
``internal_error``.

Both tests spawn a *real* subprocess via ``PiHarness.spawn`` against a
fake ``pi`` script — the bug is in how the subprocess StreamReader is
constructed, so mocking ``stdout`` would not exercise the fix.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from relay.config import Settings
from relay.harness.pi import PiHarness
from relay.harness.protocol import SessionEnded

_LINE_BYTES = 200 * 1024  # 200 KiB — comfortably over the 64 KiB default


def _write_fake_pi(tmp_path: Path, payload_bytes: int) -> Path:
    """Write a fake `pi` executable that ignores its args and emits two
    JSONL events: a small ``session`` line then a large ``agent_end``
    whose ``messages[0].content`` is ``payload_bytes`` of ``x``."""
    script = tmp_path / "fake_pi.py"
    script.write_text(
        "#!" + sys.executable + "\n"
        "import json, sys\n"
        'print(json.dumps({"type": "session", "id": "s", "cwd": "/tmp"}), flush=True)\n'
        f"big = 'x' * {payload_bytes}\n"
        'print(json.dumps({"type": "agent_end", "messages": ['
        '{"role": "assistant", "content": big}]}), flush=True)\n'
    )
    script.chmod(0o755)
    return script


def test_pi_session_handles_jsonl_line_over_64kib(tmp_path: Path) -> None:
    """End-to-end through PiHarness.spawn: a 200 KiB JSONL line must
    parse without raising."""

    async def scenario() -> list[object]:
        fake_pi = _write_fake_pi(tmp_path, _LINE_BYTES)
        settings = Settings(pi_bin=str(fake_pi), data_dir=tmp_path / ".relay")
        harness = PiHarness(settings)
        session = await harness.spawn(
            prompt="ignored",
            cwd=tmp_path,
            env={},
            signal_config=None,
        )
        events: list[object] = []
        async for ev in session.events():
            events.append(ev)
        await session.wait()
        return events

    events = asyncio.run(scenario())

    # SessionStarted + SessionEnded must both be present. Without the
    # fix, the iteration raises ValueError from asyncio.StreamReader
    # before SessionEnded is reached.
    ended = [e for e in events if isinstance(e, SessionEnded)]
    assert ended, f"no SessionEnded yielded; got {[type(e).__name__ for e in events]}"
    msgs = ended[0].messages
    assert len(msgs) == 1
    assert isinstance(msgs[0], dict)
    assert len(msgs[0]["content"]) == _LINE_BYTES


def test_settings_pi_stdout_limit_default_is_generous() -> None:
    """Default must comfortably exceed pi's plausible single-line size
    (large tool results, verbose agent_end.messages). 8 MiB chosen as
    the documented default — generous, bounded, far above 64 KiB."""
    s = Settings(data_dir=Path("/tmp/relay-test"))
    assert s.pi_stdout_limit >= 8 * 1024 * 1024


def test_settings_pi_stdout_limit_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The ``RELAY_PI_STDOUT_LIMIT`` env var overrides the default."""
    monkeypatch.setenv("RELAY_PI_STDOUT_LIMIT", str(123_456))
    s = Settings(data_dir=Path("/tmp/relay-test"))
    assert s.pi_stdout_limit == 123_456


# Sanity: the test setup itself is correct — without our fix, the test
# above would fail with ValueError; without a large enough payload, it
# would pass trivially. Guard the threshold so a future refactor that
# weakens the default also weakens this test in a visible way.
def test_regression_payload_exceeds_asyncio_default() -> None:
    """The fixture must actually exceed the 64 KiB default that triggers
    the bug; if this drops, the regression test stops protecting us."""
    asyncio_default = 2**16  # asyncio.streams._DEFAULT_LIMIT
    assert _LINE_BYTES > asyncio_default
