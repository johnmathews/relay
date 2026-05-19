"""OTel mirror of the event store (spec.md §10, ADR-29).

A deliberately thin instrumentation layer. The orchestrator holds one
:class:`Instrumentation`; it is *additive* — when
``RELAY_OTEL_EXPORT=none`` :func:`build_instrumentation` returns the
literal :data:`NOOP` (no SDK provider, no exporter, no global OTel
state, no network — the OTel SDK import is paid only on the langfuse
path) and every span helper is a no-op. The langfuse path builds its
**own** :class:`TracerProvider` (never the process-global one) so
embedding relay never commandeers a host app's OTel setup and tests
stay isolated.

Span tree (mirrors the event store; ADR-10 — never a second source):

    relay.run            (RelayCore._run try/finally — closes even on crash)
    └── relay.iter        (per run_loop iteration; relay.iter_seq = iters.seq)
        └── relay.tool_call (per ToolUseEnd; timed from event ts)

GenAI/usage attributes are set on the iter span from
``SessionEnded.messages[].usage`` (ADR-18 — the only token/cost source)
**only when present**; absent fields are omitted, never zero-filled.
"""

from __future__ import annotations

import base64
from collections.abc import Iterator, Sequence
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol

from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
    OTLPSpanExporter,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace import (
    Span,
    Status,
    StatusCode,
    Tracer,
    set_span_in_context,
)

from relay_v2.config import Settings

__all__ = [
    "Instrumentation",
    "RunSpan",
    "IterSpan",
    "NoopInstrumentation",
    "OtelInstrumentation",
    "NOOP",
    "NOOP_RUN_SPAN",
    "NOOP_ITER_SPAN",
    "build_instrumentation",
]


# ── protocols ──────────────────────────────────────────────────────────


class IterSpan(Protocol):
    """Per-iter span handle. Tool spans hang off it."""

    def record_tool_call(
        self,
        *,
        name: str,
        tool_id: str,
        is_error: bool,
        duration_ms: int,
        start_ts: float,
        end_ts: float,
    ) -> None: ...

    def set_usage(self, messages: Sequence[Any]) -> None: ...

    def set_exit(self, exit_reason: str) -> None: ...


class RunSpan(Protocol):
    """Per-run span handle. Yields child iter spans."""

    def iter_span(
        self, *, seq: int, phase: str | None
    ) -> AbstractContextManager[IterSpan]: ...


class Instrumentation(Protocol):
    def run_span(
        self, run_id: str
    ) -> AbstractContextManager[RunSpan]: ...

    def shutdown(self) -> None: ...


# ── no-op (the RELAY_OTEL_EXPORT=none path) ────────────────────────────


class _NoopIterSpan:
    def record_tool_call(self, **_: Any) -> None:
        pass

    def set_usage(self, messages: Sequence[Any]) -> None:
        pass

    def set_exit(self, exit_reason: str) -> None:
        pass


class _NoopRunSpan:
    @contextmanager
    def iter_span(
        self, *, seq: int, phase: str | None
    ) -> Iterator[IterSpan]:
        yield NOOP_ITER_SPAN


class NoopInstrumentation:
    """Literal no-op: constructs no provider/exporter, touches no global
    state, makes no network call (ADR-29 risk surface)."""

    @contextmanager
    def run_span(self, run_id: str) -> Iterator[RunSpan]:
        yield NOOP_RUN_SPAN

    def shutdown(self) -> None:
        pass


NOOP_ITER_SPAN: IterSpan = _NoopIterSpan()
NOOP_RUN_SPAN: RunSpan = _NoopRunSpan()
NOOP: Instrumentation = NoopInstrumentation()


# ── usage aggregation (ADR-18 payload shape) ───────────────────────────


def _num(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _aggregate_usage(messages: Sequence[Any]) -> dict[str, Any]:
    """Sum assistant-message ``usage`` across an iter's messages. Every
    key is included only if pi actually surfaced it — never zero-filled
    (plan.md Phase-7 risk; ADR-18 is authority on the shape)."""
    totals = {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0,
              "totalTokens": 0}
    seen = {k: False for k in totals}
    cost = 0.0
    has_cost = False
    system: str | None = None
    model: str | None = None

    for m in messages:
        if not isinstance(m, dict) or m.get("role") != "assistant":
            continue
        if isinstance(m.get("provider"), str):
            system = m["provider"]
        if isinstance(m.get("model"), str):
            model = m["model"]
        usage = m.get("usage")
        if not isinstance(usage, dict):
            continue
        for key in totals:
            if _num(usage.get(key)):
                totals[key] += usage[key]
                seen[key] = True
        c = usage.get("cost")
        if isinstance(c, dict) and _num(c.get("total")):
            cost += c["total"]
            has_cost = True

    attrs: dict[str, Any] = {}
    if system is not None:
        attrs["gen_ai.system"] = system
    if model is not None:
        attrs["gen_ai.request.model"] = model
    if seen["input"]:
        attrs["gen_ai.usage.input_tokens"] = totals["input"]
    if seen["output"]:
        attrs["gen_ai.usage.output_tokens"] = totals["output"]
    if seen["cacheRead"]:
        attrs["relay.usage.cache_read_tokens"] = totals["cacheRead"]
    if seen["cacheWrite"]:
        attrs["relay.usage.cache_write_tokens"] = totals["cacheWrite"]
    if seen["totalTokens"]:
        attrs["relay.usage.total_tokens"] = totals["totalTokens"]
    if has_cost:
        attrs["relay.usage.cost_usd"] = cost
    return attrs


# ── OTel-backed implementation (the langfuse path) ─────────────────────


class _OtelIterSpan:
    def __init__(self, tracer: Tracer, span: Span, ctx: Any) -> None:
        self._tracer = tracer
        self._span = span
        self._ctx = ctx  # this iter is the current span → parent of tools

    def record_tool_call(
        self,
        *,
        name: str,
        tool_id: str,
        is_error: bool,
        duration_ms: int,
        start_ts: float,
        end_ts: float,
    ) -> None:
        s = self._tracer.start_span(
            "relay.tool_call",
            context=self._ctx,
            start_time=int(start_ts * 1e9),
            attributes={
                "relay.tool_name": name,
                "relay.tool_id": tool_id,
                "relay.tool_is_error": is_error,
                "relay.tool_duration_ms": duration_ms,
            },
        )
        if is_error:
            s.set_status(Status(StatusCode.ERROR))
        s.end(end_time=int(end_ts * 1e9))

    def set_usage(self, messages: Sequence[Any]) -> None:
        for key, value in _aggregate_usage(messages).items():
            self._span.set_attribute(key, value)

    def set_exit(self, exit_reason: str) -> None:
        self._span.set_attribute("relay.exit_reason", exit_reason)


class _OtelRunSpan:
    def __init__(
        self, tracer: Tracer, span: Span, ctx: Any, run_id: str
    ) -> None:
        self._tracer = tracer
        self._span = span
        self._ctx = ctx
        self._run_id = run_id

    @contextmanager
    def iter_span(
        self, *, seq: int, phase: str | None
    ) -> Iterator[IterSpan]:
        attrs: dict[str, Any] = {
            "relay.run_id": self._run_id,
            "relay.iter_seq": seq,
        }
        if phase is not None:
            attrs["relay.phase"] = phase
        span = self._tracer.start_span(
            "relay.iter", context=self._ctx, attributes=attrs
        )
        child_ctx = set_span_in_context(span, self._ctx)
        try:
            yield _OtelIterSpan(self._tracer, span, child_ctx)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            span.end()


class OtelInstrumentation:
    """Owns a private (non-global) TracerProvider. The processor is
    injected so tests pass an ``InMemorySpanExporter`` and production
    passes a ``BatchSpanProcessor(OTLPSpanExporter)`` (ADR-29)."""

    def __init__(
        self, processor: SpanProcessor, *, service_name: str = "relay-v2"
    ) -> None:
        self._provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        self._provider.add_span_processor(processor)
        self._tracer = self._provider.get_tracer("relay_v2.observability")

    @contextmanager
    def run_span(self, run_id: str) -> Iterator[RunSpan]:
        span = self._tracer.start_span(
            "relay.run", attributes={"relay.run_id": run_id}
        )
        ctx = set_span_in_context(span)
        try:
            yield _OtelRunSpan(self._tracer, span, ctx, run_id)
        except BaseException as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR))
            raise
        finally:
            span.end()

    def shutdown(self) -> None:
        self._provider.shutdown()


# ── factory ────────────────────────────────────────────────────────────


def build_instrumentation(settings: Settings) -> Instrumentation:
    """Pick the instrumentation from ``RELAY_OTEL_EXPORT`` (spec.md §11).

    ``none`` → :data:`NOOP` (no exporter constructed). ``langfuse`` →
    OTLP/HTTP to ``{host}/api/public/otel/v1/traces`` with HTTP Basic
    ``base64("{public}:{secret}")`` — the researched Langfuse
    self-hosted contract (ADR-29), fail-fast on missing keys.
    """
    mode = settings.otel_export
    if mode == "none":
        return NOOP
    if mode == "langfuse":
        host = settings.langfuse_host
        pk = settings.langfuse_public_key
        sk = settings.langfuse_secret_key
        missing = [
            name
            for name, val in (
                ("RELAY_LANGFUSE_HOST", host),
                ("RELAY_LANGFUSE_PUBLIC_KEY", pk),
                ("RELAY_LANGFUSE_SECRET_KEY", sk),
            )
            if not val
        ]
        if missing:
            raise ValueError(
                "RELAY_OTEL_EXPORT=langfuse requires "
                + ", ".join(missing)
            )
        assert host is not None and pk is not None and sk is not None
        endpoint = host.rstrip("/") + "/api/public/otel/v1/traces"
        token = base64.b64encode(f"{pk}:{sk}".encode()).decode()
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={"Authorization": f"Basic {token}"},
        )
        return OtelInstrumentation(BatchSpanProcessor(exporter))
    raise ValueError(
        f"unknown RELAY_OTEL_EXPORT={mode!r} (expected 'none' or "
        "'langfuse')"
    )
