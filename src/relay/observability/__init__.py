"""OTel mirror of the event store (Phase 7, spec.md §10, ADR-29).

Opt-in via ``RELAY_OTEL_EXPORT``; a strict no-op when off. The event
store remains the single source of truth (ADR-10) — this only mirrors.
"""

from relay.observability.otel import (
    NOOP,
    NOOP_ITER_SPAN,
    NOOP_RUN_SPAN,
    Instrumentation,
    IterSpan,
    IterSpanContext,
    NoopInstrumentation,
    OtelInstrumentation,
    RunSpan,
    build_instrumentation,
)

__all__ = [
    "NOOP",
    "NOOP_ITER_SPAN",
    "NOOP_RUN_SPAN",
    "Instrumentation",
    "IterSpan",
    "IterSpanContext",
    "NoopInstrumentation",
    "OtelInstrumentation",
    "RunSpan",
    "build_instrumentation",
]
