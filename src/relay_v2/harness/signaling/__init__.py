"""Signaling strategies (ADR-05).

``text_sentinels`` is the MVP strategy on pi; ``mcp_tools`` is a
post-MVP stub. The orchestrator emits a normalized
:class:`~relay_v2.harness.protocol.SignalEmitted` regardless of which
strategy produced it.
"""

from relay_v2.harness.protocol import SignalConfig, SignalEmitted
from relay_v2.harness.signaling.sentinels import MarkerError, detect_in_text

__all__ = ["SignalConfig", "SignalEmitted", "MarkerError", "detect_in_text"]
