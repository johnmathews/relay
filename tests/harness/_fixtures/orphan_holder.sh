#!/bin/sh
# Fake "pi" for tests/harness/test_pi_session_cancel_kills_descendants.
# Forks a backgrounded sleeper, announces its pid as a JSONL line on
# stdout (so the test can verify the killpg cascade reaches it), then
# blocks. Without Layer 1's start_new_session, killing this "pi" alone
# leaves the sleeper running and holding the stdout pipe's write end
# open — the live bug from run 20260604-201957-62d5.
#
# Responds to --version so PiHarness._maybe_check_version does not hang.
if [ "$1" = "--version" ]; then
    echo "0.0.0-test-fixture"
    exit 0
fi
sleep 100 &
SLEEPER_PID=$!
printf '{"type":"session","id":"test","cwd":"/tmp","sleeper_pid":%s}\n' "$SLEEPER_PID"
exec sleep 100
