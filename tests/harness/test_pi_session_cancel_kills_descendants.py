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
