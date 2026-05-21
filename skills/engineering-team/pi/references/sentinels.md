# Sentinels reference

This is the authoritative description of the sentinel contract that the
engineering-team skill emits and relay parses. The grammar is unchanged
from v1 (spec.md §12 mandates a verbatim port of the v1 `text_sentinels`
strategy); this doc mirrors it for skill consumers and adds the
operational "when to emit" guidance the skill needs.

> Contract source in relay-v2: `docs/spec.md` §4.3 (signaling) and §12
> (skill port). The harness's text-sentinel parser implements this
> grammar; its fixtures port from v1's `tests/test-parsing.sh`.

## Format

A sentinel is a single line anchored at column 0 (no leading whitespace), of
the form:

    [[engteam:<verb> <key="value" ...>]]

The seven verbs:

- `phase-start` — entering a new phase. Required attrs: `phase="<name>"` where
  name is one of `evaluation`, `planning`, `development`, `wrap-up`.
- `unit-start` — beginning a work unit (Phase 3 only). Required attrs:
  `id="W<n>"`, `title="<title>"`.
- `unit-done` — work unit is green. Required attrs: `id="W<n>"`, `title="<title>"`.
- `unit-abandoned` — work unit cannot complete in this session. Required
  attrs: `id="W<n>"`, `reason="<short reason>"`.
- `handoff` — closing sentinel, more work remains. No attrs.
- `done` — closing sentinel, plan exhausted. No attrs.
- `pause-for-input` — closing sentinel, user input required. Required attrs:
  `id="P<n>"`, `question="<one-line summary>"`.

## When to emit phase-start

Emit `phase-start` immediately after loading the matching `phases/phase-N-<name>.md`
file, before any other work in that phase. If a single iter spans multiple
phases (e.g. Phase 1 → Phase 2 after a clean synthesis), emit `phase-start`
once per phase entered, in order.

The driver writes the LAST `phase-start` value seen in the iter to
`$RELAY_RUN_DIR/phase`, and injects it into the next iter's preamble as
`RELAY_PHASE: <name>`. That preamble is your authoritative signal for which
phase doc to load on the next iter.

## Closing sentinels

`handoff`, `done`, and `pause-for-input` are the three closing sentinels.
Exactly one must appear at the end of every iter; the driver bails with exit 1
if more than one appears or none appear.

`handoff` and `pause-for-input` require a marker-bracketed next-session prompt
(see "Prompt markers" below) immediately before them — the driver extracts
that body and writes it back to the prompt file for the next iter. There is
no LLM-side reformatting; the body is taken verbatim.

`done` does not need a prompt body. Emitting prompt markers before `done`
is a contract violation; the driver exits 1.

## Prompt markers

The next-session prompt for `handoff` and `pause-for-input` is delimited
by two line-anchored marker sentinels in the existing `[[engteam:...]]`
namespace:

    [[engteam:prompt-start]]
    [[engteam:prompt-end]]

These are **markers**, not verbs — they do not appear in the seven-verb
closing-sentinel decision tree and they never close an iter. The body
between the matched pair is the next-iter prompt, captured verbatim.

You can put anything between the markers, including fenced code blocks
at any depth. The markers free you from the old "no fences in the prompt
body" rule — author Markdown freely.

### Pairing rules you must obey

- Emit **exactly one** `prompt-start` / `prompt-end` pair before
  `handoff` or `pause-for-input`. Missing pair → driver exits 1.
- Emit them **in order**: `prompt-start` first, `prompt-end` second.
- `prompt-end` must be the **last non-blank line before the closing
  sentinel**. Only blank lines may separate them. Stray content between
  `prompt-end` and `handoff` / `pause-for-input` aborts the run.
- **Never** emit `prompt-start` / `prompt-end` before `done`. `done`
  has no prompt body. The driver rejects this combination.

### Error messages you may see

The driver emits these one-line headlines on parse errors, each
followed by a multi-line repair recipe printed to stderr:

- `extract_handoff_prompt: no [[engteam:prompt-end]] preceding [[engteam:handoff]]`
- `extract_handoff_prompt: content between [[engteam:prompt-end]] and closing sentinel`
- `extract_handoff_prompt: [[engteam:prompt-end]] without matching [[engteam:prompt-start]]`
- `extract_pause_prompt:` — same three messages, prefixed differently.
- `[[engteam:done]] cannot have prompt markers (found prompt-start/end pair preceding)`

The repair recipe printed after the headline contains a literal
correct-shape template and a one-line callout naming the pre-2026-05-17
fenced-block convention as the most common cause of this rejection.
For the `done` case the recipe inverts — it says `done` takes no
prompt body and shows the bare sentinel form.

Example stderr for a handoff parse error:

    extract_handoff_prompt: no [[engteam:prompt-end]] preceding [[engteam:handoff]]

    The closing sentinel must be preceded by a marker pair. Required shape
    (every marker line must be at column 0):

        [[engteam:prompt-start]]
        <next-session prompt body — any markdown, including fenced code blocks>
        [[engteam:prompt-end]]

        [[engteam:handoff]]

    If your iter ended with a triple-backtick fenced block immediately
    before [[engteam:handoff]], that is the pre-2026-05-17 convention and
    the driver no longer accepts it. Wrap the prompt body in the marker
    pair above.

    See: skills/engineering-team/pi/references/sentinels.md

> Your phase doc (`phases/phase-N-<name>.md`) contains the literal
> closing-emission template you should copy from when emitting your
> iter's closing sentinel. The worked example below is for reference;
> the in-phase templates are the working surface.

### Worked example: handoff prompt containing a fenced bash block

```text
... narrative ...

[[engteam:prompt-start]]
Phase 4 wrap-up — emit phase-start phase="wrap-up" and follow
~/.claude/skills/engineering-team/phases/phase-4-wrap-up.md.

Run the final gate:

```bash
uv run ruff check . && uv run mypy && uv run pytest -q
```

Then merge.
[[engteam:prompt-end]]

[[engteam:handoff]]
```

## Who emits

Only the lead engineer (you, the single session) emits sentinels in the
user-facing transcript. relay-v2's MVP runs one session per iter with no
subagent dispatch, so there is no subagent text to worry about. When relay
later gains subagent dispatch (spec.md §12, post-MVP), dispatched children
must NOT emit sentinels — only the top-level session's assistant text is
parsed by the driver.

## Anti-mention rule

The matcher is line-anchored: any line in assistant text that begins at
column 0 with `[[engteam:...]]` matches, regardless of surrounding markdown.
Putting a sentinel inside a fenced code block in your reply does NOT hide it
from the parser — the fence is markdown, the matcher reads raw lines.

If you must illustrate sentinel syntax in chat:

- Indent the example by at least one space so the line no longer starts at
  column 0, OR
- Write the example into a file via the Write tool (file contents are
  invisible to the parser), OR
- Echo it via a Bash command (tool inputs are invisible to the parser).

## When to emit `pause-for-input`

Emit `pause-for-input` for non-trivial decisions that genuinely need human
input before continuing:

1. Architecturally distinct alternatives where both have merit and the choice
   has downstream consequences.
2. Changes that would alter the user's stated non-goals.
3. Anything irreversible (data deletion, schema migrations not authorized by
   the plan, force-pushes).
4. Public-API changes the plan didn't explicitly mandate.

Do NOT emit `pause-for-input` for:

1. Variable / function naming; formatting; comment style.
2. Two implementations that produce identical observable behavior.
3. Minor refactors inside a unit that don't change scope.
4. Anything the plan already implicitly authorizes.

## How to pause

1. Stop work on the current unit. Do not continue past the decision point.
2. Do not merge the worktree or end the cycle. Leave the worktree intact so
   the resumed session can read code/files there.
3. Emit a marker-bracketed next-session prompt (same shape as a handoff
   prompt — wrap the body in `[[engteam:prompt-start]]` /
   `[[engteam:prompt-end]]`) so the resumed session has full context.
4. Immediately after `prompt-end` (only blank lines allowed between),
   emit on its own line:
   `[[engteam:pause-for-input id="P<n>" question="<one-line summary>"]]`
   where `<n>` is the next free pause ID (P1 first, P2 second, ...). The
   question must be one line; multi-line context goes in the prompt body.
5. End the session. The user will resume the loop with relay's resume
   flag, supplying the answer.
