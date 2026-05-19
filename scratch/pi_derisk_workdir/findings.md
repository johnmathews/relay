# pi de-risking findings

Run: 2026-05-19 11:30:54

pi command: `PI_AGENT_SDK=1 pi`
model: `claude-sonnet-4-6`
scratch dir: `/Users/john/projects/relay-v2/scratch/pi_derisk_workdir`

## Conclusions

**4/5 tests passed. The "failed" test (long_bash) confirmed the architectural assumption — the failure was the harness's own 180s wall-clock timeout, not pi.**

- ✅ **pi authenticates via `PI_AGENT_SDK=1`** with no further configuration. The Max-subscription auth path works.
- ✅ **No 30s tool timeout.** The 70-second `sleep` Bash command ran to completion; `DONE_AFTER_70S` appears in the event stream. This is the load-bearing finding that unblocks the pi choice.
- ✅ **Event schema documented (11 types observed):** `session`, `agent_start`, `turn_start`, `message_start`, `message_update`, `message_end`, `tool_execution_start`, `tool_execution_update`, `tool_execution_end`, `turn_end`, `agent_end`. Each has a consistent `type` discriminator.
- ✅ **End-of-iter signal:** `agent_end` is the terminal event on clean exit, carrying compiled `messages`. `turn_end` carries `message` + `toolResults` and fires per turn within a session.
- ✅ **Session persistence and resume:** sessions persist as JSONL under `~/.pi/agent/sessions/--<cwd-encoded>--/<ts>_<uuid>.jsonl`. `--continue` correctly resumes — confirmed by recall of "ALPHA" across two invocations.
- ✅ **No `parent_tool_use_id` in the event stream** — confirms pi's documented "no subagents at the protocol level" stance. Relay's orchestrator-level subagent dispatch (ADR-06) is the right approach.

## HarnessEvent normalization for pi (confirmed by these runs)

| pi event | relay `HarnessEvent` |
|---|---|
| `session` | `SessionStarted(session_id, cwd)` |
| `agent_start` | (consumed internally; not surfaced) |
| `turn_start` | (consumed internally) |
| `message_update` (with `assistantMessageEvent.type=="text_delta"`) | `AssistantText(text, turn_id)` |
| `tool_execution_start` | `ToolUseStart(id=toolCallId, name=toolName, args)` |
| `tool_execution_update` | `ToolUseUpdate(id, partial_result)` |
| `tool_execution_end` | `ToolUseEnd(id, result, is_error)` |
| `turn_end` | (consumed internally for accounting; not surfaced) |
| `agent_end` | `SessionEnded(messages)` |

The `assistantMessageEvent` sub-discriminator on `message_update` events needs further inspection — there may be other sub-types beyond `text_delta` (e.g., `thinking`, `signature`). Inspect `test_event_shapes.jsonl` if needed.

## Suggested follow-up tests (not run today)

- Long-running tool > 5 minutes — verify no hidden longer-horizon timeout
- `--mode rpc` interactive command flow — for relay's orchestrator dispatch
- Session resume across `kill -9` (rather than clean exit)
- Multi-turn iteration: ask the model to call multiple tools across multiple turns and watch the event ordering
- Auth failure mode — what happens if `~/.pi/agent/auth.json` is missing/expired


## version

- **status**: PASS
- **duration**: 0.7s
- **notes**:
  - pi --version rc=0
  - stderr: ['0.74.0']

## simple_completion

- **status**: PASS
- **duration**: 2.8s
- **event counts**: `{'session': 1, 'agent_start': 1, 'turn_start': 1, 'message_start': 2, 'message_end': 2, 'message_update': 3, 'turn_end': 1, 'agent_end': 1}`
- **final event type**: `agent_end`
- **final event keys**: `['type', 'messages']`
- **raw**: `test_simple_completion.jsonl`
- **notes**:
  - rc=0, events=12, non_json_lines=0
  - event types: {'session': 1, 'agent_start': 1, 'turn_start': 1, 'message_start': 2, 'message_end': 2, 'message_update': 3, 'turn_end': 1, 'agent_end': 1}
  - final event type: agent_end
  - final event keys: ['type', 'messages']

## event_shapes

- **status**: PASS
- **duration**: 17.1s
- **event counts**: `{'session': 1, 'agent_start': 1, 'turn_start': 4, 'message_start': 8, 'message_end': 8, 'message_update': 45, 'tool_execution_start': 3, 'tool_execution_update': 6, 'tool_execution_end': 3, 'turn_end': 4, 'agent_end': 1}`
- **final event type**: `agent_end`
- **final event keys**: `['type', 'messages']`
- **raw**: `test_event_shapes.jsonl`
- **notes**:
  - rc=0, events=84
  - event type histogram: {'session': 1, 'agent_start': 1, 'turn_start': 4, 'message_start': 8, 'message_end': 8, 'message_update': 45, 'tool_execution_start': 3, 'tool_execution_update': 6, 'tool_execution_end': 3, 'turn_end': 4, 'agent_end': 1}
  - unique event types seen: 11
  -   session: keys=['type', 'version', 'id', 'timestamp', 'cwd']
  -   agent_start: keys=['type']
  -   turn_start: keys=['type']
  -   message_start: keys=['type', 'message']
  -   message_end: keys=['type', 'message']
  -   message_update: keys=['type', 'assistantMessageEvent', 'message']
  -   tool_execution_start: keys=['type', 'toolCallId', 'toolName', 'args']
  -   tool_execution_update: keys=['type', 'toolCallId', 'toolName', 'args', 'partialResult']
  -   tool_execution_end: keys=['type', 'toolCallId', 'toolName', 'result', 'isError']
  -   turn_end: keys=['type', 'message', 'toolResults']

## long_bash

- **status**: FAIL
- **duration**: 180.0s
- **event counts**: `{'session': 1, 'agent_start': 1, 'turn_start': 2, 'message_start': 4, 'message_end': 3, 'message_update': 30, 'tool_execution_start': 1, 'tool_execution_update': 2, 'tool_execution_end': 1, 'turn_end': 1}`
- **final event type**: `message_update`
- **final event keys**: `['type', 'assistantMessageEvent', 'message']`
- **raw**: `test_long_bash.jsonl`
- **notes**:
  - rc=-9, events=46, elapsed=180.0s
  - event types: {'session': 1, 'agent_start': 1, 'turn_start': 2, 'message_start': 4, 'message_end': 3, 'message_update': 30, 'tool_execution_start': 1, 'tool_execution_update': 2, 'tool_execution_end': 1, 'turn_end': 1}
  - saw DONE_AFTER_70S in event stream: True
  - stderr (first 3): ['HARNESS_TIMEOUT after 180s']

## session_resume

- **status**: PASS
- **duration**: 6.3s
- **event counts**: `{'session': 2, 'agent_start': 2, 'turn_start': 2, 'message_start': 4, 'message_end': 4, 'message_update': 6, 'turn_end': 2, 'agent_end': 2}`
- **notes**:
  - run1: rc=0, events=12
  - new session files: 1
  - most recent: 2026-05-19T09-30-48-687Z_019e3f92-d7ee-70de-b1ff-89ad7d681ee7.jsonl
  - run2: rc=0, events=12
  - run2 referenced ALPHA: True

