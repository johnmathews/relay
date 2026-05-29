"""relay v2 harness layer.

Public surface (ADR-04): the normalized protocol/event types and the
concrete :class:`~relay.harness.pi.PiHarness`. Nothing outside this
package may import pi's JSONL schema.
"""

from relay.harness.protocol import (
    AssistantText,
    AssistantTextDelta,
    Harness,
    HarnessEvent,
    HarnessSession,
    SessionEnded,
    SessionStarted,
    SignalConfig,
    SignalEmitted,
    ToolUseEnd,
    ToolUseStart,
    ToolUseUpdate,
)

__all__ = [
    "AssistantText",
    "AssistantTextDelta",
    "Harness",
    "HarnessEvent",
    "HarnessSession",
    "SessionEnded",
    "SessionStarted",
    "SignalConfig",
    "SignalEmitted",
    "ToolUseEnd",
    "ToolUseStart",
    "ToolUseUpdate",
]
