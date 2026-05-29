"""``mcp_tools`` signaling strategy — stub only (ADR-05 §5.2).

On pi this strategy needs the ``pi-mcp-adapter`` community extension and
is explicitly *not built* in the MVP. The strategy hook exists so the
orchestrator's strategy switch has a second arm; selecting it raises
:class:`NotImplementedError` rather than silently degrading.
"""

from __future__ import annotations

from relay.harness.protocol import SignalConfig, SignalEmitted

__all__ = ["detect_in_tool"]

_NOT_BUILT = (
    "mcp_tools signaling is not built in the MVP (ADR-05 §5.2). Use "
    "strategy='text_sentinels'. The mcp_tools arm requires the "
    "pi-mcp-adapter community extension and is a post-MVP feature."
)


def detect_in_tool(
    tool_name: str, config: SignalConfig
) -> SignalEmitted | None:  # pragma: no cover - stub
    """Would map a ``relay__*`` tool call to a :class:`SignalEmitted`.
    Unimplemented in the MVP."""
    raise NotImplementedError(_NOT_BUILT)
