# Plan — Phase 9f (OTel span parenting across runs)

**Status:** ready to execute
**Date:** 2026-05-22
**Source proposal:** `docs/proposals/parallel-iters-fanout-join.md` (sub-phase 9f, the §"Observability" sketch)
**Predecessors:** 9a (cascade helper + `awaiting_children`, PR #2 / 4ebb1f8), 9b (dispatch, PR #3 / 381c147), 9c (join watcher, PR #4 / 37b8cb7), 9d (runtime cancel-cascade, PR #5 / 4a910b4), 9e (dashboard Children pane, PR #6 / e43f05b)
**Successors:** skill-side fanout guidance (separate small follow-up PR — `skills/engineering-team/pi/references/fanout.md` + phase-doc cross-links, deferred from 9e); latent ADR-10 gap on `agent_end`/`SessionEnded` persistence (separately parked)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Goal

Close the fanout-join arc by wiring the OTel half: a child run's `relay.run` span is parented on the dispatching iter's `relay.iter` span (same `trace_id`; `parent_span_id == dispatching_iter.span_id`). A Langfuse trace tree of a fanned-out run becomes one connected tree —

```
relay.run            (parent, pre-fanout phase)
├── relay.iter (seq=1, normal)
└── relay.iter (seq=2, fanout-closing)         ← dispatching iter
    ├── relay.run (child A)
    │   ├── relay.iter (child A seq=1)
    │   └── relay.iter (child A seq=2, done)
    ├── relay.run (child B)
    │   └── relay.iter (child B seq=1, done)
    └── relay.run (parent, synthesizer phase)  ← re-enqueued post-join, parented on the dispatching iter
        └── relay.iter (seq=3, synthesizer)
```

— instead of today's four disconnected sub-trees (parent-pre-fanout, child A, child B, parent-synthesizer-phase) that share nothing in Langfuse.

(The parent has *two* `relay.run` spans across its fanout-join lifecycle because `_maybe_resume_parent` replaces the `_RunState` at `core.py:618` and `_run` runs a second time; this is a pre-existing 9c artefact. 9f links both phases under the dispatching iter so the whole fanout-join cycle is one connected tree.)

The work is strictly additive to the existing OTel mirror (Phase 7, ADR-29). The `Instrumentation` seam is extended; no new exporter, no schema change, no new event kinds, no sentinel grammar change, no harness contract change (ADR-04 invariant intact). The `RELAY_OTEL_EXPORT=none` path stays a literal no-op — no provider, no exporter, no network, no behavioural drift.

After 9f:

1. `Instrumentation.run_span(run_id, *, parent_iter_ctx=None)` — a new keyword-only optional parameter accepting an opaque carrier (`IterSpanContext`, a type alias of `Any` defined in `observability/otel.py`). `None` (the default, and the value at the top of every fresh-run code path) preserves today's behaviour: the run span is started as a trace root. A non-`None` value is the OTel `Context` of the dispatching iter; `OtelInstrumentation` passes it as `context=` to `tracer.start_span`, producing a nested span under the iter.
2. `IterSpan` protocol gains a `.context` property returning the same opaque `IterSpanContext`. For `_NoopIterSpan` it is `None`; for `_OtelIterSpan` it is the OTel `Context` produced internally by `set_span_in_context(span, parent_ctx)` (which `_OtelRunSpan.iter_span` already constructs as `child_ctx`).
3. `LoopResult` gains an `Optional[IterSpanContext]` field `fanout_parent_ctx`, populated only when the loop returns `awaiting_children`. The loop captures `iter_span.context` *inside* the closing iter's `with` block (before the iter span ends) and stashes it on the result.
4. `RelayCore._RunState` gains an `Optional[IterSpanContext]` field `parent_iter_ctx`. `RelayCore._apply_result`, on the `awaiting_children` branch, threads `result.fanout_parent_ctx` into `_dispatch_children`, which sets `state.parent_iter_ctx` on each child's `_RunState` before enqueuing. `_run` reads `state.parent_iter_ctx` and passes it to `self._otel.run_span(ctx.run_id, parent_iter_ctx=…)`.
5. A scripted-harness fanout test in `tests/observability/test_otel_export.py` exercises the full path under `InMemorySpanExporter`: asserts each child's `relay.run` span has `parent.span_id == dispatching_iter.span_id` and the entire fanned-out tree shares one `trace_id`.
6. The manual Langfuse-UI smoke procedure in `docs/observability.md` is extended with a "trace-tree across fanout" subsection: run a fanout, screenshot the connected tree, attest in a dated journal entry. Gated the same way ADR-29's existing manual checks are (mirrors ADR-30's automated-vs-manual split).

After 9f, the entire fanout-join arc (9a → 9f) is shipped.

## Architecture

**The choice — Option A (LoopResult-threaded carrier).** The dispatching iter's OTel `Context` is captured inside the iter's `with` block in `run_loop`, returned on `LoopResult.fanout_parent_ctx`, threaded through `RelayCore._apply_result` → `_dispatch_children` → each child's `_RunState.parent_iter_ctx`, and finally consumed by `_run` calling `self._otel.run_span(ctx.run_id, parent_iter_ctx=state.parent_iter_ctx)`. Every hop already exists in the call graph; the new field rides along as `Optional[Any]`. No new shared mutable map on `RelayCore`; no schema migration; no `traceparent` column. Rejected alternatives (in-memory dict on `RelayCore` keyed by `child_run_id`; persistent `traceparent` column on `runs`) are recorded in ADR-38 with rationale.

**The opaque carrier — `IterSpanContext = Any`.** Defined in `observability/otel.py` and re-exported. The concrete object is OTel's `Context` (the W3C-propagation immutable container — same thing `set_span_in_context(span, parent_ctx)` returns inside `_OtelRunSpan.iter_span` today as `child_ctx`). Everywhere outside `observability/otel.py` treats it as `Any`: the loop accepts and round-trips it, `RelayCore` stashes and forwards it, `_run` hands it back to the mirror. Only `OtelInstrumentation.run_span` and `_OtelIterSpan.context` know its real type. This keeps the OTel mirror's reach into the orchestrator a single opaque value, not a leakage of OTel types into orchestrator layers.

**Capture timing — inside the iter's `with`.** The loop captures `iter_span.context` after `_drive_iter` returns and before the closing `iter_span.set_exit(...)` / iter's `with` block exits. Strictly speaking the OTel `Context` is immutable and the underlying `SpanContext` (trace_id/span_id/trace_flags/trace_state) remains valid forever — capturing it after `span.end()` would also be safe per spec — but capturing *inside* the `with` is the conservative reading and the obvious place a reader's eye lands. The 9b detection path (`signal.kind == "fanout"`) is the only branch where capture matters; every other exit path leaves `fanout_parent_ctx=None` (the LoopResult default).

**The NOOP path — byte-for-byte unchanged.** `_NoopIterSpan.context` returns the literal `None`. `NoopInstrumentation.run_span` accepts the new keyword-only argument and ignores it (literally: it never touches the value). `OtelInstrumentation.run_span` passes `parent_iter_ctx` straight through to `tracer.start_span(name, context=parent_iter_ctx)` — and OTel's API documents `context=None` as "use the current context", which for a fresh thread/task is the empty default → root span. So a `None` arrival on the OTel path also yields the existing root-span behaviour. No conditional branching needed inside `OtelInstrumentation`.

**The synthesizer phase — the parent's *second* `relay.run` span, also parented under the dispatching iter.** Pre-existing 9c artefact: `_maybe_resume_parent` (core.py:501) replaces `self._runs[parent_id]` with a fresh `_RunState()` at line 618 and re-enqueues the parent. The supervisor invokes `_run` a second time; today that produces a brand-new root `relay.run` span disconnected from the parent's original (pre-fanout) run-span. 9f closes that gap with the *same* mechanism it uses for children: preserve the dispatching iter's context from the parent's old `_RunState.result.fanout_parent_ctx` *before* the overwrite at line 618, set it on the fresh `_RunState.parent_iter_ctx`, and let `_run` thread it into `self._otel.run_span(...)`. Result: the synthesizer-phase `relay.run` is parented on the dispatching iter, alongside the children. The fanout-join cycle reads as one connected sub-tree rooted at the dispatching iter.

**The synthesizer iter — no special marking.** Inside the synthesizer-phase `relay.run`, the synthesizer iter is a normal iter (`relay.iter_seq == closing.seq + 1`); its span nests under the synthesizer-phase `relay.run` via the existing `_OtelRunSpan.iter_span` path. No new attribute on the synthesizer iter; no span link from synthesizer to children — both would duplicate parentage already in the tree.

**The dispatching iter's `set_exit` value.** Today the closing fanout iter receives `iter_span.set_exit("signal")` (loop.py:358) — the same call any signal-terminated iter receives. 9f does not change that. The iter's role as fanout dispatcher is visible from the `signal_emit` event's `kind: "fanout"` and from the `subagent_dispatch` children events that follow on the same iter_id; it is not encoded in the OTel mirror, which (per ADR-10) only mirrors the event store.

**Recursive fanout.** Child runs can themselves fanout (up to `max_fanout_depth`). When a grandchild dispatches, the same capture-and-thread happens on the child's loop: the child's dispatching iter captures *its* context, which itself is nested under the parent's iter span. The trace tree therefore reflects the full recursion depth. No new code is needed for the recursive case — the mechanism is per-iter.

**Restart with parent in `awaiting_children`.** The 9a startup cascade cancels every descendant under the parent (ADR-34 V1 non-goal: cross-restart fanout). Span linkage is lost across the restart — the new process has no in-memory `_RunState.parent_iter_ctx`, no closed-but-still-valid OTel `Context` from the previous process's tracer. Acceptable: the cascade-cancel writes the final `run_ended` rows; the next Langfuse view of those runs will simply show three disconnected `relay.run` trees with their final ERROR state. Recorded in ADR-38 and called out in spec.md §10.

**`_apply_result` — where the context enters `_RunState`.** Choice of hop: the alternative is for `_run` to read `result.fanout_parent_ctx` directly off the parent's `LoopResult`, but that crosses a run boundary (child's `_run` reading parent's result), which would re-introduce exactly the kind of cross-run shared-state we are avoiding. The 9b/9c pattern — `_apply_result` derives downstream-state from `result` and threads it into `_dispatch_children`, which seeds each child's `_RunState` — is the established shape. 9f follows it: `_dispatch_children(..., parent_iter_ctx=...)` writes `state.parent_iter_ctx` on each new `_RunState` *before* enqueueing (mirror of the 9c "create-all-rows-then-enqueue" two-pass invariant — the supervisor cannot pick up a child whose state has not been fully populated).

**Why no global TracerProvider.** ADR-29's invariant. The OTel mirror owns its own non-global `TracerProvider`, exporting OTLP/HTTP only on the `langfuse` path. 9f does not touch that. The cross-run parenting works entirely inside the relay process — both parent and child runs use the *same* tracer, so the `Context` is portable across runs without any propagation wire format.

**Tech stack.** No new runtime deps. Uses the existing pins: `opentelemetry-api`, `opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-http`, all `>=1.27,<2`. The `<2` cap is precautionary per ADR-29; 9f does not approach it.

## File map

| file | action | one-line responsibility |
|---|---|---|
| `src/relay_v2/observability/otel.py` | modify | add `IterSpanContext` type alias; extend `IterSpan` protocol with `.context` (property); extend `Instrumentation.run_span` with kwarg-only `parent_iter_ctx`; mirror on `RunSpan` no change; implement `.context` on `_NoopIterSpan` (`None`) and `_OtelIterSpan` (the captured `Context`); forward `parent_iter_ctx` to `tracer.start_span(..., context=...)` in `OtelInstrumentation.run_span` |
| `src/relay_v2/orchestrator/loop.py` | modify | capture `iter_span.context` inside the closing iter's `with` block when `signal.kind == "fanout"`; populate `LoopResult.fanout_parent_ctx` on the awaiting_children return path |
| `src/relay_v2/orchestrator/loop.py` (LoopResult dataclass) | modify | add `fanout_parent_ctx: IterSpanContext | None = None` after `fanout_payload` |
| `src/relay_v2/core.py` | modify | `_RunState` gains `parent_iter_ctx: IterSpanContext | None = None`; `_apply_result.awaiting_children` extracts `result.fanout_parent_ctx` and passes to `_dispatch_children`; `_dispatch_children` accepts `parent_iter_ctx` and writes it onto each child's `_RunState`; `_maybe_resume_parent` preserves the old `_RunState.result.fanout_parent_ctx` across the line-618 overwrite and writes it onto the parent's fresh `_RunState.parent_iter_ctx`; `_run` reads `state.parent_iter_ctx` and passes to `self._otel.run_span(ctx.run_id, parent_iter_ctx=…)` |
| `tests/observability/test_otel_export.py` | modify | add cross-run-parentage tests: scripted fanout → assert child `relay.run` spans have `parent.span_id == dispatching_iter.span_id` and shared `trace_id`; assert recursive fanout (grandchild) yields a 3-level tree; assert NOOP path stays no-network; assert non-fanout runs remain root spans (regression) |
| `tests/orchestrator/test_relay_core.py` | modify | unit test that `_RunState.parent_iter_ctx` is wired correctly from `_dispatch_children` (using a stub `Instrumentation` that records what it sees) |
| `tests/orchestrator/test_loop.py` | modify | unit test that `LoopResult.fanout_parent_ctx` is populated on the awaiting_children path and `None` on every other terminal path |
| `docs/spec.md` | modify | §10 (Observability) — add the cross-run parenting paragraph + the trace-tree diagram; cross-reference ADR-38 |
| `docs/decisions.md` | append | ADR-38 — Cross-run trace context lives in-memory, threaded via `LoopResult` → `_RunState` |
| `docs/observability.md` | modify | extend manual-acceptance section with "trace tree across fanout" sub-procedure (run a fanout, verify connected tree, journal-attest) |
| `docs/plans/2026-05-22-fanout-join-9f.md` | create | this plan doc |
| `CLAUDE.md` | modify | extend "Current state" with a 9f paragraph; bump source-file count if it changes (it should not — no new modules) |

**No new source modules.** Everything is additive to existing files. The `Instrumentation` seam (`observability/otel.py`) is the only OTel-layer file touched. The orchestrator layer touches `loop.py` and `core.py` only. No frontend changes (the trace tree is rendered by Langfuse, not relay's dashboard). No new dependencies.

## ADR claim

**ADR-38 is pre-claimed.** The cross-run trace context routing decision is load-bearing enough to record:

- *(decision)* The dispatching iter's OTel `Context` is captured inside the closing iter's `with` block in `run_loop`, returned on `LoopResult.fanout_parent_ctx`, threaded through `RelayCore._apply_result` → `_dispatch_children` → each child's `_RunState.parent_iter_ctx`, and consumed by `_run` calling `self._otel.run_span(ctx.run_id, parent_iter_ctx=…)`.
- *(decision)* The carrier is an opaque `IterSpanContext = Any` type alias defined in `observability/otel.py`. Only the OTel mirror inspects the real type (the OTel `Context`); the loop and core round-trip it as `Any`.
- *(decision)* The synthesizer-phase `relay.run` (the parent's second `_run` invocation, created by `_maybe_resume_parent`) is also parented under the dispatching iter — *not* as a separate trace root. The same `parent_iter_ctx` carrier preserved from the parent's old `_RunState` is stashed on the fresh `_RunState` at line 618. Result: dispatching iter has children + synthesizer-phase parent.run as descendants, one connected tree. The synthesizer iter inside that span is unmarked (normal iter; no `relay.iter_kind` attribute).
- *(decision)* Restart with a parent in `awaiting_children` loses cross-run span linkage; accepted because the 9a cascade-cancel finalises the tree (ADR-34 V1 non-goal of cross-restart fanout).
- *(rejected)* In-memory dict on `RelayCore` keyed by `child_run_id`. Rejected: introduces a new mutable shared map alongside `_runs`/`_tasks`/`_enqueue_lock` with silent-leak failure mode and hides the producer→consumer coupling behind a dict key.
- *(rejected)* Persistent `traceparent` column on `runs`. Rejected: V1 non-goal; pushes OTel mirror state into the source-of-truth schema (uncomfortable per ADR-10's framing); requires a migration for no observable benefit.
- *(rejected)* Span links from the synthesizer iter to each child run. Rejected: redundant with the parent-iter→child-run parentage already in the tree; would complicate `IterSpan`/`RunSpan` protocols with a `SpanLink`-add method.

## Open contract questions

**OCQ-1 — Should the captured `Context` be deep-copied before stashing on `_RunState`, in case OTel mutates it?**

OTel's `Context` is immutable by design: every `set_span_in_context` returns a new `Context` rather than mutating in-place (this is what enables W3C propagation safely). Capturing the reference is safe — no mutation can race the child `_run` reading it. **Resolution: no deep copy.** Add a test assertion that the captured `Context` equals the dispatching iter's at the time the child starts (i.e., they share the same trace/span identity).

**OCQ-2 — Should `_NoopIterSpan.context` be a property or an attribute?**

The protocol declares `.context` (no parens — read-only access). Implementations are free to back it with a property, an attribute, or `__getattr__`. The simplest is a class attribute `context = None` on `_NoopIterSpan` (Python looks up `instance.context` → falls back to class attribute → returns `None`, with no descriptor magic). **Resolution: class attribute** for the noop, **property** for the OTel impl (it reads `self._ctx`). Recorded here so the implementing task knows which to pick.

**OCQ-3 — What if a non-fanout iter exits with `signal.kind=="done"` while the loop has nonetheless captured an iter context?**

The proposed implementation captures the iter context *only on the `fanout` branch* (i.e., the assignment to `LoopResult.fanout_parent_ctx` is gated by `signal.kind == "fanout"`). For any other terminal kind, the field stays `None`. **Resolution: gated capture.** Test asserts every non-fanout terminal yields `fanout_parent_ctx is None` to prevent a future refactor from accidentally leaking a captured context into a non-fanout return.

**OCQ-4 — Should the synthesizer-phase parent.run span be parented under the dispatching iter (alongside children) or be a separate root?**

**Resolution: parent under the dispatching iter.** (Recorded as Task 4b; ADR-38 captures the decision.) The pre-existing 9c artefact is that `_maybe_resume_parent` replaces the parent's `_RunState` at line 618 and `_run` opens a fresh `relay.run` span on resume. 9f preserves the dispatching iter's context across that overwrite so the synthesizer-phase run-span lives under the same dispatching iter as the children — the fanout-join cycle reads as one connected sub-tree. Rejected alternative: leave the synth-phase as a separate root (worse Langfuse UX: join phase visually disconnected from the dispatch that triggered it). Rejected alternative: defer to a follow-up sub-phase 9g (leaves a known gap shipped to main; the synth-phase parenting is cheap enough — ~5 lines + one test — to land in 9f).

**OCQ-5 — Should the `_run` "cancelled-before-start" guard (9d, the `run_row.status in (done, failed, cancelled)` early-return branch) still create a `relay.run` span?**

Today's `_run` (post-9d) returns early *before* the `with self._otel.run_span(...)` block. After 9f, the same guard sits in the same place, before the `with`. The span is never created for a cascade-DB-finalised descendant — which is correct, because that descendant never ran. The OTel mirror has nothing to mirror. **Resolution: keep the guard above the `with` block.** The cascade-DB-finalised branch leaves no `relay.run` span behind; the in-flight-cancel branch (which does enter the `with`) gets its run span finalised with `ERROR` status via the existing `BaseException`-records-exception path. Recorded here to head off "why doesn't this cancelled child appear in the trace tree" investigations.

**OCQ-6 — When recursive fanout is in play (a child itself fans out into grandchildren), does the grandchild's `parent_iter_ctx` chain back correctly to the original parent's iter?**

Yes, by construction. Each generation's dispatching iter captures *its own* iter span's context; OTel's `Context` carries the full trace_id + a unique span_id at each level. The grandchild's `relay.run` is parented on the child's dispatching iter, which is itself nested under the child's `relay.run`, which is nested under the parent's dispatching iter, which is nested under the parent's `relay.run`. The trace tree shows the full lineage. **Resolution: no special handling; assert in a dedicated test.** A scripted three-level fanout (parent → child → grandchild) is included in the test plan to lock this in.

**OCQ-7 — Should the `Instrumentation` factory signature stay the same, or grow new parameters?**

`build_instrumentation(settings)` stays the same — no new settings, no new env vars. The new behaviour is entirely a property of the `run_span` callsite. **Resolution: factory signature unchanged.**

## Tasks

> Subagent-driven execution: each task is one TDD cycle (failing test → impl → green test → review checkpoint). The order is dependency-driven; do not parallelise across tasks.

### Task 1 — OTel mirror: protocol + opaque carrier

- [ ] Add `IterSpanContext = Any` type alias in `src/relay_v2/observability/otel.py` (immediately under the imports). Add to `__all__`.
- [ ] **Test first** (`tests/observability/test_otel_export.py`): write `test_noop_iter_span_context_is_none` — calls `NOOP.run_span("r1")` → `run_span.iter_span(seq=1, phase=None)` → `iter_span.context is None`.
- [ ] **Test first**: write `test_otel_iter_span_context_carries_trace_identity` — build an `OtelInstrumentation` with `InMemorySpanExporter`; open a run span and an iter span; assert `iter_span.context` is non-None and that constructing a new span with `context=iter_span.context` produces a span whose `parent.span_id` matches the iter's span_id (via `InMemorySpanExporter.get_finished_spans()` after span end).
- [ ] Run the new tests → expect AttributeError / NotImplementedError on `.context`. Confirms gates are real.
- [ ] **Implement**: extend the `IterSpan` Protocol with `context: IterSpanContext` (property typing — `@property def context(self) -> IterSpanContext: ...`).
- [ ] **Implement**: add `context = None` (class attribute) to `_NoopIterSpan`.
- [ ] **Implement**: add `@property def context(self) -> IterSpanContext: return self._ctx` to `_OtelIterSpan`.
- [ ] Re-run new tests → green.
- [ ] Run `uv run pytest tests/observability/` and `uv run mypy --strict src/relay_v2/observability/` → both green.

### Task 2 — OTel mirror: parent_iter_ctx kwarg on Instrumentation.run_span

- [ ] **Test first** (`tests/observability/test_otel_export.py`): write `test_otel_run_span_accepts_parent_iter_ctx_kwarg_default_none` — opening `instrumentation.run_span("r1")` (no kwarg) still produces a root span (no parent). This asserts the default behaviour is unchanged.
- [ ] **Test first**: write `test_otel_run_span_parents_under_parent_iter_ctx` — open a run+iter span on parent run "P"; capture `iter_span.context`; close the iter+run; then call `instrumentation.run_span("C", parent_iter_ctx=captured)`; assert the C run-span's `parent.span_id == P_iter.span_id` and `trace_id == P.trace_id` (via `InMemorySpanExporter`).
- [ ] **Test first**: write `test_noop_run_span_ignores_parent_iter_ctx` — calling `NOOP.run_span("r1", parent_iter_ctx=object())` does not raise and produces the same NOOP RunSpan. This locks the NOOP signature compatibility.
- [ ] Run new tests → expect TypeError on unknown kwarg.
- [ ] **Implement**: extend `Instrumentation` Protocol with `def run_span(self, run_id: str, *, parent_iter_ctx: IterSpanContext = None) -> AbstractContextManager[RunSpan]: ...`.
- [ ] **Implement**: `NoopInstrumentation.run_span` accepts `*, parent_iter_ctx: IterSpanContext = None` and ignores it.
- [ ] **Implement**: `OtelInstrumentation.run_span` accepts `*, parent_iter_ctx: IterSpanContext = None` and passes it as `context=parent_iter_ctx` to `self._tracer.start_span("relay.run", context=parent_iter_ctx, attributes=…)`.
- [ ] Re-run tests → green.
- [ ] Run `uv run pytest tests/observability/` + `uv run mypy --strict` → green.
- [ ] **Review checkpoint**: read the diff. Confirm the NOOP branch makes no OTel calls. Confirm `_OtelIterSpan.context` is a simple property over `self._ctx`. Confirm `__all__` is consistent.

### Task 3 — Loop: LoopResult.fanout_parent_ctx + capture on the fanout branch

- [ ] **Test first** (`tests/orchestrator/test_loop.py`): write `test_loop_result_fanout_parent_ctx_default_none` — every non-fanout LoopResult terminal path (done, failed, cancelled, paused) returns a result with `fanout_parent_ctx is None`. Parameterised over the existing scripted fixtures.
- [ ] **Test first**: write `test_loop_captures_iter_context_on_fanout_terminal` — drive the loop against a scripted harness that emits `[[engteam:fanout]]` + a fanout-start/end JSON block; pass an `OtelInstrumentation` with `InMemorySpanExporter`; assert the returned `LoopResult.fanout_parent_ctx` is non-None and that a downstream `run_span(..., parent_iter_ctx=result.fanout_parent_ctx)` produces a span whose `parent.span_id` equals the dispatching iter's span_id.
- [ ] Run new tests → expect AttributeError on `fanout_parent_ctx`.
- [ ] **Implement**: add `fanout_parent_ctx: IterSpanContext | None = None` to the `LoopResult` dataclass in `src/relay_v2/orchestrator/loop.py` (after `fanout_payload`). Import `IterSpanContext` from `relay_v2.observability.otel`.
- [ ] **Implement**: in `run_loop`, on the `signal.kind == "fanout"` branch, capture `iter_span.context` *before* the iter's `with` block exits, and pass it as `fanout_parent_ctx=…` on the `LoopResult("awaiting_children", …)` return. Add an inline ADR-38 comment naming the invariant.
- [ ] Re-run tests → green.
- [ ] Run `uv run pytest tests/orchestrator/` + `uv run ruff check src/relay_v2/orchestrator/loop.py` + `uv run mypy --strict src/relay_v2/orchestrator/` → green.
- [ ] **Review checkpoint**: read the loop diff. Confirm capture is gated by `signal.kind == "fanout"`. Confirm no other terminal path mutates `fanout_parent_ctx`.

### Task 4 — RelayCore: thread parent_iter_ctx through _apply_result + _dispatch_children + _RunState + _run

- [ ] **Test first** (`tests/orchestrator/test_relay_core.py`): write `test_dispatch_children_stashes_parent_iter_ctx_on_child_state` — using a stub `Instrumentation` that records every `run_span` call with its `parent_iter_ctx`, drive a parent run that fans out; assert each child's `_RunState.parent_iter_ctx` matches the parent's `LoopResult.fanout_parent_ctx`, and that the stub's recorded `run_span(child_id, parent_iter_ctx=…)` call carries the same object.
- [ ] **Test first**: write `test_non_fanout_runs_pass_none_parent_iter_ctx` — a normal (done) run causes `run_span(run_id)` to be called with `parent_iter_ctx=None` (or unset). Regression guard.
- [ ] Run new tests → expect AttributeError on `_RunState.parent_iter_ctx`.
- [ ] **Implement**: add `parent_iter_ctx: IterSpanContext | None = None` to `_RunState`. Import `IterSpanContext`.
- [ ] **Implement**: extend `_dispatch_children` signature with `parent_iter_ctx: IterSpanContext | None = None`. Inside the loop that builds each child's `_RunState`, set `self._runs[child_run_id].parent_iter_ctx = parent_iter_ctx` *before* the row is enqueued (mirrors the 9c create-all-then-enqueue invariant).
- [ ] **Implement**: in `_apply_result`'s `awaiting_children` branch, pass `parent_iter_ctx=result.fanout_parent_ctx` to `_dispatch_children`.
- [ ] **Implement**: in `_run`, change the `with self._otel.run_span(ctx.run_id) as run_span:` line to `with self._otel.run_span(ctx.run_id, parent_iter_ctx=state.parent_iter_ctx) as run_span:`. Add an inline ADR-38 comment.
- [ ] Re-run tests → green.
- [ ] Run `uv run pytest tests/orchestrator/` + `uv run ruff check src/relay_v2/core.py` + `uv run mypy --strict src/relay_v2/core.py` → green.
- [ ] **Review checkpoint**: read the core.py diff. Confirm the cancelled-before-start guard (9d) sits above the `with` block — no span is opened for cascade-DB-finalised descendants. Confirm the synth-phase wiring is *not yet* added (Task 4b is its own cycle).

### Task 4b — RelayCore: synth-phase parenting on _maybe_resume_parent

- [ ] **Test first** (`tests/observability/test_otel_export.py` or `tests/orchestrator/test_fanout_join_integration.py`): write `test_synthesizer_phase_runspan_is_parented_under_dispatching_iter` — using a scripted fanout that completes through the synthesizer phase, assert there are exactly *two* `relay.run` spans named for the parent run_id (pre-fanout phase + synthesizer phase), and that the synthesizer-phase one has `parent.span_id == dispatching_iter.span_id`. Same `trace_id` as the rest of the tree.
- [ ] Run new test → expect failure (synth-phase span is currently a root).
- [ ] **Implement**: in `_maybe_resume_parent`, *before* `self._runs[parent_run_id] = _RunState()` at line 618, capture `old_state = self._runs.get(parent_run_id)` and extract `preserved_ctx = old_state.result.fanout_parent_ctx if old_state and old_state.result else None`. After constructing the fresh `_RunState()`, write `self._runs[parent_run_id].parent_iter_ctx = preserved_ctx`. Add an inline ADR-38 comment naming the invariant ("synth phase parents under the dispatching iter, alongside children").
- [ ] Re-run test → green.
- [ ] Run `uv run pytest tests/observability/ tests/orchestrator/` + `uv run ruff check src/relay_v2/core.py` + `uv run mypy --strict src/relay_v2/core.py` → green.
- [ ] **Review checkpoint**: confirm the preserve-then-overwrite is exception-safe (a missing `old_state.result` falls back to `None`, the existing behaviour). Confirm cancelled-via-cascade-9d still terminates the parent correctly when it's mid-`awaiting_children` (the cancelled-before-start guard in `_run` short-circuits before opening the span; the lost synth-phase span is intentional).

### Task 5 — End-to-end OTel span-tree integration test

- [ ] **Test first** (`tests/observability/test_otel_export.py`): write `test_fanout_produces_connected_trace_tree` — using `InMemorySpanExporter` + a `ScriptedHarness` that emits `[[engteam:fanout]]` with two children, drive a parent run through `RelayCore` to completion; after the synthesizer iter finalises the parent, collect all finished spans; assert:
  - Exactly one root span (no `parent`): the parent's *pre-fanout* `relay.run`.
  - Each child's `relay.run` has `parent.span_id` == the dispatching iter's span_id.
  - The synthesizer-phase `relay.run` (the parent's second run-span) ALSO has `parent.span_id` == the dispatching iter's span_id.
  - All spans share one `trace_id`.
  - The synthesizer iter is parented under the synthesizer-phase `relay.run` (not under the dispatching iter directly).
- [ ] **Test first**: write `test_recursive_fanout_produces_three_level_tree` — parent → child → grandchild; assert the grandchild's `relay.run` chains back to the parent's run via two iter spans.
- [ ] **Test first**: write `test_noop_path_makes_no_otel_calls_on_fanout` — run the same fanout against `NoopInstrumentation`; assert no provider/exporter is constructed. Use the existing NOOP-isolation pattern (no-import check on `opentelemetry.sdk` after the run; or inject a sentinel-recording instrumentation).
- [ ] Run new tests → expect failures (the trees should still be disconnected because Task 4's wire-up is what connects them — these tests verify it end-to-end).
- [ ] **No new implementation expected** — Tasks 1–4 should make these pass. If they don't, fix the wire-up.
- [ ] Run `uv run pytest tests/observability/ tests/orchestrator/` → all green.
- [ ] **Review checkpoint**: read the integration tests. Confirm the assertions verify the *correct* parentage shape (not just "non-None parent"). Confirm `trace_id` equality is asserted explicitly.

### Task 6 — Documentation: spec.md §10 + ADR-38

- [ ] **Implement**: append ADR-38 to `docs/decisions.md`, body per the "ADR claim" section above; use the existing ADR headline format (`## ADR-38 — Cross-run trace context: in-memory, threaded via LoopResult → _RunState (Phase 9f)`); status `accepted`; date `2026-05-22`; record the rejected alternatives + the restart caveat verbatim.
- [ ] **Implement**: extend `docs/spec.md` §10 (Observability) with the cross-run parenting paragraph + the ASCII trace-tree diagram from the Goal section. Reference ADR-38. Update the `relay.run` → `relay.iter` → `relay.tool_call` sketch to note that `relay.run` is the trace root *except* when the run is a fanout child, in which case it is parented on the dispatching iter.
- [ ] **Implement**: extend `docs/observability.md` "Manual acceptance" subsection with a "Trace tree across fanout" sub-procedure — run a scripted fanout against a live Langfuse instance (or a real `[[engteam:fanout]]`-emitting run), open the Langfuse UI, verify the connected tree (one trace_id, child runs rooted under the dispatching iter, synthesizer iter sibling). Journal-attest the result; gated like `PI_INTEGRATION=1` (ADR-30 pattern).
- [ ] Run `uv run pytest` (full backend gate) + `uv run ruff check .` + `uv run mypy --strict src/` → all green (no source changes in this task, but a final gate catches anything Task 5 didn't).
- [ ] **Review checkpoint**: read the spec.md diff. Confirm §10 has no contradictions with the ADR-29 framing (the OTel mirror is never a second source of truth — 9f does not change that). Confirm ADR-38 references ADR-29, ADR-34, ADR-35 by number.

### Task 7 — CLAUDE.md + plan-doc finalisation

- [ ] **Implement**: extend the "Current state" section of `CLAUDE.md` with a 9f paragraph following the 9e paragraph's density and shape. Mention: the new `Instrumentation.run_span(parent_iter_ctx=…)` kwarg, the `LoopResult.fanout_parent_ctx` + `_RunState.parent_iter_ctx` plumbing, the `IterSpanContext` opaque carrier, ADR-38, the NOOP-unchanged invariant, and the test count delta. State that the entire fanout-join arc (9a→9f) is now shipped.
- [ ] **Implement**: bump source-file count in CLAUDE.md only if a new module was added (none should be — the count should stay at **39**). Bump test count from 278 to whatever Task 5's additions produce.
- [ ] Run the final gate one more time: `uv run pytest && uv run ruff check . && uv run mypy --strict src/` — all green.
- [ ] Note in the CLAUDE.md paragraph: skill-side fanout docs (`skills/engineering-team/pi/references/fanout.md`) are still TODO as a follow-up PR (deferred from 9e).

## Verification commands

```bash
# Full backend gate (the executing skill's after-each-task verification)
uv run pytest                               # all green; new tests in tests/observability/ + tests/orchestrator/
uv run ruff check .                         # clean
uv run mypy --strict src/                   # clean; 39 source files (no new modules)

# Fast loop for OTel work
uv run pytest tests/observability/          # InMemorySpanExporter; offline
uv run pytest tests/orchestrator/test_loop.py tests/orchestrator/test_relay_core.py

# Spec sanity (no shape change, but verifies openapi still parses)
uv run pytest tests/api/test_openapi.py

# Frontend gate — unchanged, but run once at the end to confirm no accidental coupling
cd frontend && npm run check                # eslint + vue-tsc + vitest, all green
```

**Manual acceptance** (ADR-30 / ADR-29 pattern; journal-attested):

1. Start a self-hosted Langfuse (see `docs/langfuse-compose.example.yml`).
2. `RELAY_OTEL_EXPORT=langfuse RELAY_LANGFUSE_HOST=… RELAY_LANGFUSE_PUBLIC_KEY=… RELAY_LANGFUSE_SECRET_KEY=… uv run relay serve`.
3. Start a real run that emits `[[engteam:fanout]]` (or a scripted harness piped through a live `RelayCore`).
4. Open the Langfuse UI → confirm: one trace_id, parent `relay.run` at the root, dispatching `relay.iter` with two child `relay.run` sub-trees, synthesizer `relay.iter` as a later sibling of the dispatching iter.
5. Screenshot or note in a `journal/260522-9f-langfuse-acceptance.md` entry.

## Out of scope

- Persistent `traceparent` column on `runs`. Cross-restart fanout span linkage (ADR-34 V1 non-goal — orphan sweep cascade-cancels).
- Span links from the synthesizer iter to each child run. Semantic tagging of the synthesizer iter (no `relay.iter_kind` attribute).
- OTel 2.x bump. Sub-iter-level cross-run spans (e.g., `relay.tool_call` → child-run linkage — tool calls cannot fanout).
- `agent_end`/`SessionEnded` event-store persistence gap. Its own ADR + spec §6 change when picked up — not 9f's concern.
- `skills/engineering-team/pi/references/fanout.md` + phase-doc cross-links. Deferred from 9e (UI-only); pick up as a small separate PR after 9f.
- Frontend changes. The dashboard does not render the OTel trace tree; Langfuse does.
- New env vars / new settings. `build_instrumentation(settings)` signature unchanged.
- New MCP tool surface. The MCP tools (`relay__*`) operate on the event store, not on OTel spans (ADR-10 — OTel is never a second source).

## Risks

- **OTel `Context` lifetime across span end.** The captured `Context` references an iter span that ends before the children dispatch. OTel's `Context` is immutable and the underlying `SpanContext` (trace_id/span_id/trace_flags/trace_state) remains valid forever — but capturing *inside* the iter's `with` block (Task 3) is the conservative reading. The integration test (Task 5) verifies parentage end-to-end; a future OTel version that mutates `Context` semantics would surface there.
- **NOOP-path regression.** Adding `.context` to the `IterSpan` Protocol risks the NOOP path constructing something. `_NoopIterSpan.context = None` (class attribute) keeps the NOOP literally a no-op. Task 5's `test_noop_path_makes_no_otel_calls_on_fanout` asserts no provider/exporter is constructed under the NOOP path. The ADR-29 risk surface (no provider, no exporter, no global state, no network) is preserved.
- **Synthesizer-iter parenting confusion.** A reader may expect the synthesizer iter to be parented under the dispatching iter (the "join follows the fanout"). It is not — it is a sibling iter of the dispatching iter under the parent's run span. Task 6 documents this explicitly in spec.md §10 and Task 5's assertions lock it in.
- **Recursive fanout depth.** Tested in Task 5's `test_recursive_fanout_produces_three_level_tree`. The depth bound (`max_fanout_depth`) caps the recursion at 4 (hard cap); the OTel mirror handles arbitrary depth correctly because each generation's iter span is a normal child of the prior generation's run span.
- **`mypy --strict` and `Any`.** `IterSpanContext = Any` deliberately surfaces no OTel types outside the mirror module. `mypy --strict` should accept `Any`-typed kwargs without comment. If a future strictness bump rejects `Any`, replace with `IterSpanContext = "Context | None"` (string-forward-reference) — but for 9f, `Any` is correct.
- **`opentelemetry-semantic-conventions`.** Deliberately NOT a dep (ADR-29 — unstable). 9f uses no new attribute keys; all attributes are existing `relay.*` literals. No risk surface change.

## Effort

~one focused session. Surface area is small (one observability module, three orchestrator call sites in two files, one test file plus three small test additions, two short docs updates, one ADR). The plan doc itself is longer than the implementation. Subagent-driven execution should land Tasks 1–4 in one TDD pass; Task 5 is the integration acceptance; Tasks 6–7 are docs. Single PR, squash-merge, title `Phase 9f: OTel span parenting across runs`.
