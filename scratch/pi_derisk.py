#!/usr/bin/env python3
"""
Pi harness empirical de-risking script.

Verifies relay v2's pi assumptions before locking the spec. Tests pi's
CLI/RPC contract end-to-end with real invocations.

Run from any directory. Writes:
  pi_derisk_workdir/findings.md   — human-readable report
  pi_derisk_workdir/test_*.jsonl  — raw JSONL captures per test

Usage:
    python3 pi_derisk.py [--skip name1,name2] [--model claude-sonnet-4-6]
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

PI_BIN = "pi"
DEFAULT_MODEL = "claude-sonnet-4-6"


@dataclass
class TestResult:
    name: str
    passed: bool = False
    notes: list[str] = field(default_factory=list)
    event_counts: Counter = field(default_factory=Counter)
    final_event: dict | None = None
    duration_s: float = 0.0
    raw_path: Path | None = None

    def note(self, msg: str) -> None:
        print(f"    [{self.name}] {msg}")
        self.notes.append(msg)


def run_pi(
    args: list[str],
    *,
    cwd: Path,
    timeout_s: float = 120,
    capture_path: Path | None = None,
    env_extra: dict[str, str] | None = None,
) -> tuple[int, list[dict], list[str], list[str]]:
    """Spawn pi, stream stdout (JSONL), capture stderr. Return:
    (returncode, parsed_events, non_json_stdout_lines, stderr_lines)."""
    env = {**os.environ, "PI_AGENT_SDK": "1"}
    if env_extra:
        env.update(env_extra)

    cmd = [PI_BIN, *args]
    print(f"  $ PI_AGENT_SDK=1 {' '.join(shlex.quote(c) for c in cmd)}  (cwd={cwd.name})")

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )

    events: list[dict] = []
    non_json: list[str] = []
    stderr_lines: list[str] = []
    cap_fh = capture_path.open("w") if capture_path else None

    def reader_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if cap_fh:
                cap_fh.write(line + "\n")
                cap_fh.flush()
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                non_json.append(line[:300])

    def reader_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line.rstrip("\n"))

    t_out = threading.Thread(target=reader_stdout, daemon=True)
    t_err = threading.Thread(target=reader_stderr, daemon=True)
    t_out.start()
    t_err.start()

    try:
        rc = proc.wait(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        rc = -9
        stderr_lines.append(f"HARNESS_TIMEOUT after {timeout_s}s")

    t_out.join(timeout=5)
    t_err.join(timeout=5)
    if cap_fh:
        cap_fh.close()

    return rc, events, non_json, stderr_lines


def classify(events: list[dict]) -> Counter:
    c: Counter = Counter()
    for ev in events:
        t = ev.get("type") or next(iter(ev.keys()), "<empty>")
        c[str(t)] += 1
    return c


# --- tests -------------------------------------------------------------------


def test_version(scratch: Path, model: str) -> TestResult:
    r = TestResult(name="version")
    t0 = time.monotonic()
    rc, _, _, errs = run_pi(["--version"], cwd=scratch, timeout_s=10)
    r.note(f"pi --version rc={rc}")
    if errs[:2]:
        r.note(f"stderr: {errs[:2]}")
    r.passed = rc == 0
    r.duration_s = time.monotonic() - t0
    return r


def test_simple_completion(scratch: Path, model: str) -> TestResult:
    r = TestResult(name="simple_completion")
    t0 = time.monotonic()
    raw = scratch / "test_simple_completion.jsonl"
    rc, events, non_json, errs = run_pi(
        [
            "-p",
            "Reply with exactly one word: pong",
            "--mode",
            "json",
            "--provider",
            "anthropic",
            "--model",
            model,
        ],
        cwd=scratch,
        timeout_s=120,
        capture_path=raw,
    )
    r.raw_path = raw
    r.event_counts = classify(events)
    if events:
        r.final_event = events[-1]
    r.note(f"rc={rc}, events={len(events)}, non_json_lines={len(non_json)}")
    r.note(f"event types: {dict(r.event_counts)}")
    if non_json[:3]:
        r.note(f"non-JSON stdout (first 3): {non_json[:3]}")
    if errs[:3]:
        r.note(f"stderr (first 3): {errs[:3]}")
    if events and r.final_event:
        r.note(f"final event type: {r.final_event.get('type', '<no type>')}")
        r.note(f"final event keys: {list(r.final_event.keys())[:10]}")
    r.passed = rc == 0 and bool(events)
    r.duration_s = time.monotonic() - t0
    return r


def test_long_bash(scratch: Path, model: str) -> TestResult:
    """Verify pi has no 30s tool timeout — run a 70-second Bash command."""
    r = TestResult(name="long_bash")
    t0 = time.monotonic()
    raw = scratch / "test_long_bash.jsonl"
    rc, events, non_json, errs = run_pi(
        [
            "-p",
            (
                "Use the Bash tool to run this command exactly: "
                "`sleep 70 && echo DONE_AFTER_70S`. After the Bash tool "
                "returns, reply with the single word RESULT followed by the "
                "Bash output. Do not run any other commands."
            ),
            "--mode",
            "json",
            "--provider",
            "anthropic",
            "--model",
            model,
        ],
        cwd=scratch,
        timeout_s=180,
        capture_path=raw,
    )
    r.raw_path = raw
    r.event_counts = classify(events)
    if events:
        r.final_event = events[-1]
    r.note(f"rc={rc}, events={len(events)}, elapsed={time.monotonic() - t0:.1f}s")
    r.note(f"event types: {dict(r.event_counts)}")
    saw_done = any("DONE_AFTER_70S" in json.dumps(ev) for ev in events)
    r.note(f"saw DONE_AFTER_70S in event stream: {saw_done}")
    if errs[:3]:
        r.note(f"stderr (first 3): {errs[:3]}")
    if saw_done and rc == 0:
        r.passed = True
        r.note("PASS: no 30s tool timeout observed")
    r.duration_s = time.monotonic() - t0
    return r


def test_session_resume(scratch: Path, model: str) -> TestResult:
    """Two-shot test: prompt 1 says ALPHA, prompt 2 (with --continue) should
    recall ALPHA from session history."""
    r = TestResult(name="session_resume")
    t0 = time.monotonic()
    session_dir = Path.home() / ".pi" / "agent" / "sessions"
    before = set(session_dir.rglob("*.jsonl")) if session_dir.exists() else set()

    raw1 = scratch / "test_session_resume_run1.jsonl"
    rc1, ev1, nj1, errs1 = run_pi(
        [
            "-p",
            "Reply with exactly the word ALPHA and nothing else.",
            "--mode",
            "json",
            "--provider",
            "anthropic",
            "--model",
            model,
        ],
        cwd=scratch,
        timeout_s=90,
        capture_path=raw1,
    )
    r.note(f"run1: rc={rc1}, events={len(ev1)}")

    after = set(session_dir.rglob("*.jsonl")) if session_dir.exists() else set()
    new_sessions = sorted(after - before, key=lambda p: p.stat().st_mtime)
    r.note(f"new session files: {len(new_sessions)}")
    if new_sessions:
        r.note(f"most recent: {new_sessions[-1].name}")

    raw2 = scratch / "test_session_resume_run2.jsonl"
    rc2, ev2, nj2, errs2 = run_pi(
        [
            "--continue",
            "-p",
            "What word did you reply with in your previous message?",
            "--mode",
            "json",
            "--provider",
            "anthropic",
            "--model",
            model,
        ],
        cwd=scratch,
        timeout_s=90,
        capture_path=raw2,
    )
    r.note(f"run2: rc={rc2}, events={len(ev2)}")
    r.event_counts = classify(ev1) + classify(ev2)
    saw_alpha = any("ALPHA" in json.dumps(ev) for ev in ev2)
    r.note(f"run2 referenced ALPHA: {saw_alpha}")
    if rc1 == 0 and rc2 == 0 and saw_alpha:
        r.passed = True
    r.duration_s = time.monotonic() - t0
    return r


def test_event_shapes(scratch: Path, model: str) -> TestResult:
    """Descriptive test: capture the event types and shapes for a typical
    short completion. Output feeds the spec doc."""
    r = TestResult(name="event_shapes")
    t0 = time.monotonic()
    raw = scratch / "test_event_shapes.jsonl"
    rc, events, non_json, errs = run_pi(
        [
            "-p",
            "Use the Bash tool to run: `echo hello`. Then reply OK.",
            "--mode",
            "json",
            "--provider",
            "anthropic",
            "--model",
            model,
        ],
        cwd=scratch,
        timeout_s=60,
        capture_path=raw,
    )
    r.raw_path = raw
    r.event_counts = classify(events)
    if events:
        r.final_event = events[-1]
    r.note(f"rc={rc}, events={len(events)}")
    r.note(f"event type histogram: {dict(r.event_counts)}")
    # Print sample of unique event-type shapes
    seen: dict[str, dict] = {}
    for ev in events:
        t = str(ev.get("type") or next(iter(ev.keys()), "<empty>"))
        if t not in seen:
            seen[t] = ev
    r.note(f"unique event types seen: {len(seen)}")
    for t, sample in list(seen.items())[:10]:
        keys = list(sample.keys())[:8]
        r.note(f"  {t}: keys={keys}")
    r.passed = bool(events)
    r.duration_s = time.monotonic() - t0
    return r


# --- runner ------------------------------------------------------------------


TESTS = [
    test_version,
    test_simple_completion,
    test_event_shapes,
    test_long_bash,
    test_session_resume,
]


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip", default="")
    p.add_argument("--model", default=DEFAULT_MODEL)
    p.add_argument(
        "--scratch",
        default=str(Path(__file__).parent / "pi_derisk_workdir"),
    )
    args = p.parse_args()

    scratch = Path(args.scratch).resolve()
    scratch.mkdir(parents=True, exist_ok=True)
    skips = {s.strip() for s in args.skip.split(",") if s.strip()}

    print(f"\n=== pi de-risking @ {time.strftime('%Y-%m-%d %H:%M:%S')} ===")
    print(f"  scratch: {scratch}")
    print(f"  model:   {args.model}")
    print(f"  skipping: {sorted(skips) or '(none)'}\n")

    results: list[TestResult] = []
    for fn in TESTS:
        name = fn.__name__.removeprefix("test_")
        if name in skips:
            print(f"--- {name}: SKIPPED ---\n")
            continue
        print(f"--- {name} ---")
        try:
            r = fn(scratch, args.model)
        except Exception as exc:
            r = TestResult(name=name)
            r.note(f"EXCEPTION: {type(exc).__name__}: {exc}")
        results.append(r)
        status = "PASS" if r.passed else "FAIL"
        print(f"--- {name}: {status} ({r.duration_s:.1f}s) ---\n")

    # Write findings markdown
    findings = scratch / "findings.md"
    with findings.open("w") as fh:
        fh.write(f"# pi de-risking findings\n\nRun: {time.strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        fh.write(f"pi command: `PI_AGENT_SDK=1 pi`\n")
        fh.write(f"model: `{args.model}`\n")
        fh.write(f"scratch dir: `{scratch}`\n\n")
        for r in results:
            fh.write(f"## {r.name}\n\n")
            fh.write(f"- **status**: {'PASS' if r.passed else 'FAIL'}\n")
            fh.write(f"- **duration**: {r.duration_s:.1f}s\n")
            if r.event_counts:
                fh.write(f"- **event counts**: `{dict(r.event_counts)}`\n")
            if r.final_event is not None:
                fh.write(f"- **final event type**: `{r.final_event.get('type', '<no type>')}`\n")
                fh.write(f"- **final event keys**: `{list(r.final_event.keys())[:10]}`\n")
            if r.raw_path:
                fh.write(f"- **raw**: `{r.raw_path.name}`\n")
            if r.notes:
                fh.write("- **notes**:\n")
                for n in r.notes:
                    fh.write(f"  - {n}\n")
            fh.write("\n")

    print(f"\nFindings → {findings}")
    failed = [r.name for r in results if not r.passed]
    print(f"Summary: {len(results) - len(failed)}/{len(results)} passed")
    if failed:
        print(f"  failed: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
