# 9f Langfuse acceptance — cross-run trace tree

**Date:** 2026-05-22
**Branch:** `main` (12 unpushed commits at the time of this run, including
ADR-39 from earlier in the day)

## What was verified

The manual half of the Phase-7 (ADR-29) + Phase-9f (ADR-38) acceptance
gates, gated like `PI_INTEGRATION=1` per ADR-30. The automated half
(`tests/observability/test_otel_export.py` with `InMemorySpanExporter`)
asserts the span-tree *shape* but cannot assert "renders correctly in
the Langfuse UI." This entry is the live-UI confirmation.

Both ADR-38 promises pass:

1. **One connected trace** across a fanout-join cycle (parent's
   pre-fanout phase + N child runs + synth-phase parent re-enqueue).
2. **Cross-run span parenting**: each child's `relay.run` and the
   synth-phase `relay.run` are all parented under the parent's
   dispatching iter, as siblings — not as disconnected trace roots.

The Phase-7 single-run case (one `relay.run` → `relay.iter` →
`relay.tool_call` tree with GenAI usage attributes) is also re-verified
because the dispatching iter and the synth iter are ordinary
`relay.iter` spans with the full attribute set.

## Test setup

- Langfuse: v3.175.0 (cloned `langfuse/langfuse` upstream `main` on
  2026-05-22 and ran `docker compose up -d`). One edit to that compose:
  commented out
  `postgres.ports:` to avoid a collision with a host Postgres on 5432
  (Langfuse's Postgres only needs to be reachable from `langfuse-web`,
  not from the host).
- pi: v0.74.0 (pinned per `docs/plan.md`).
- OTel SDK: `opentelemetry-*` 1.42.0 (visible in
  `telemetry.sdk.version` in the trace metadata).
- relay env: `RELAY_OTEL_EXPORT=langfuse`,
  `RELAY_LANGFUSE_HOST=http://localhost:3000`, public + secret key
  exported, `PI_AGENT_SDK=1`.
- Project registered at `/Users/john/projects/relay/relay-fanout-test`
  (a tiny scratch dir created for this test). See "Side observations"
  for the surprise that the run's worktree still landed in `relay-v2/
  .relay/worktrees/` rather than under the registered project root.
- Trigger: a deliberately scripted prompt mandating a 2-child fanout
  with trivial bodies (write a one-line greeting; the join iter
  concatenates them into `summary.md`). The goal was a deterministic
  trace-tree shape, not a realistic engineering-team Phase-1 run — the
  acceptance gate is the shape, not the work content.

## Trace verified

- **trace_id:** `c8cf8faa10017c8ef4b49b1e5f1214ba`
- **Parent run:** `20260522-180504-355f` (status `done`, 2 iters, 26
  events).
- **Children:** `20260522-180511-34de` (role `child-a`, status `done`),
  `20260522-180511-d867` (role `child-b`, status `done`).
- Wall-clock for the whole cycle: 29.80 s; cost roll-up:
  $0.005346 (parent pre-fanout iter $0.005034 + synth iter $0.000312;
  children's cost rolls into their own `relay.run` spans).

Tree shape observed in Langfuse (the synthetic 29.80 s `relay.run`
header at the very top is Langfuse's trace wrapper named after the
root span — not an extra relay span):

```
relay.run 7.54s              (parent, pre-fanout)         ← actual root span
└── relay.iter 7.43s          (parent seq=1, exit "signal") ← dispatching iter
    ├── relay.run 9.54s        (child A — child-a)
    │   └── relay.iter 9.52s
    │       └── relay.tool_call
    ├── relay.run 8.18s        (child B — child-b)
    │   └── relay.iter 8.18s
    │       └── relay.tool_call
    └── relay.run 12.71s       (synth-phase parent)
        └── relay.iter 12.71s   (parent seq=2 — the synthesizer)
            ├── relay.tool_call 0.03s   (read greeting-a.md)
            ├── relay.tool_call 0.01s   (read greeting-b.md)
            └── relay.tool_call 0.00s   (write summary.md)
```

The dispatching iter's span attributes carry real pi usage:
`gen_ai.usage.input_tokens=3`, `gen_ai.usage.output_tokens=335`,
`relay.usage.cache_write_tokens=19072`,
`relay.usage.total_tokens=19410`,
`relay.usage.cost_usd=0.076554`, plus `gen_ai.system=anthropic` and
`gen_ai.request.model=claude-sonnet-4-6`. So the OTel mirror is
attaching the GenAI semantic-convention attributes correctly even on
the terminal-sentinel close path that needed the ADR-29 Option-D
harness lookahead.

Recursive fanout (a child that itself fanouts into grandchildren) was
NOT exercised in this run. The 2-level shape is enough to verify the
parentage mechanism; ADR-38 §recursive-symmetry is held in reserve as
its own future acceptance if anyone ever changes the
`_RunState.parent_iter_ctx` plumbing. The restart-with-`awaiting_children`-parent
disconnected-trees case (ADR-38 §restart-caveat) was also not
exercised — that's a V1 non-goal per ADR-34.

## Side observations (real bugs, NOT acceptance failures)

Three real-but-orthogonal bugs surfaced during the run. None of them
block the ADR-38 acceptance, but each deserves its own follow-up:

1. **Worktree provisioned under the wrong project root.** The project
   was registered at `/Users/john/projects/relay/relay-fanout-test`,
   but the run's worktree landed at
   `/Users/john/projects/relay/relay-v2/.relay/worktrees/20260522-180504-355f`
   — relay-v2's tree, not the scratch project. Either the run was
   started against a stale project_id, or `provision_workspace`'s
   project-root resolution is picking up the wrong project. Worth
   reading `src/relay_v2/orchestrator/lifecycle.py` `provision_workspace`
   + the New-Run wizard's project_id handling.
2. **SSE didn't stream live.** The dashboard's run-detail view showed
   `No events yet` + `1/3 iters RUNNING` and required a browser
   refresh to surface the actual events. ADR-23's contract is
   no-gap-no-dup live tailing — either the browser's `EventSource`
   never connected (dev-tools network tab would say), or the
   broadcaster isn't fanning out to subscribers for this run. Worth
   reading `src/relay_v2/sse.py` Broadcaster + `api/events.py`
   `sse_event_stream` + the frontend `RunEventStream` wrapper.
3. **`UsageRow.vue` sums to zero.** The new ADR-39 timeline row
   rendered `CLEAN Σ in 0 · out 0 · cache 0` on the parent's
   synthesizer iter, despite Langfuse showing real usage attributes
   on the same iter (input=3, output=335 on the dispatching iter; the
   synth iter had its own non-zero numbers too). The SFC sums
   `payload.messages[].usage.{input_tokens,output_tokens,cache_read_input_tokens}` —
   the field-name match is probably wrong for pi's actual
   `SessionEnded.messages[]` shape. Worth opening one of the
   `harness_session_ended` event payloads from the DB and reconciling
   the field names.

## Files referenced

- `docs/observability.md` §"Trace tree across fanout" — the procedure
  followed (lines 141–196).
- `docs/decisions.md` ADR-29 (Phase 7), ADR-38 (Phase 9f cross-run
  parenting), ADR-39 (today's `harness_session_ended` persistence).
- `docs/proposals/parallel-iters-fanout-join.md` — the design intent
  behind fanout.
- `skills/engineering-team/pi/references/fanout.md` — added earlier
  today (follow-up #1); the agent followed the grammar described
  there.

## Result

**PASS.** ADR-30's manual gate for ADR-29 + ADR-38 is closed by this
entry. The fanout-join arc's observability story (one connected trace
across all the runs of a cycle, GenAI/usage attributes attached to
each iter, cost roll-up) is verified end-to-end in Langfuse's live UI
on real pi.
