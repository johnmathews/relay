"""Signaling strategies (ADR-05)."""

from relay_v2.harness.protocol import SignalConfig, SignalEmitted
from relay_v2.harness.signaling.fanout import (
    FanoutChild,
    FanoutParseError,
    FanoutPayload,
)
from relay_v2.harness.signaling.sentinels import (
    MarkerError,
    count_closing_sentinels,
    detect_in_text,
    extract_fanout_payload,
    extract_handoff_prompt,
    extract_pause_id,
    extract_pause_prompt,
    extract_pause_question,
    extract_phase_start,
    validate_done_no_prompt_markers,
)

__all__ = [
    "FanoutChild",
    "FanoutParseError",
    "FanoutPayload",
    "MarkerError",
    "SignalConfig",
    "SignalEmitted",
    "count_closing_sentinels",
    "detect_in_text",
    "extract_fanout_payload",
    "extract_handoff_prompt",
    "extract_pause_id",
    "extract_pause_prompt",
    "extract_pause_question",
    "extract_phase_start",
    "validate_done_no_prompt_markers",
]
