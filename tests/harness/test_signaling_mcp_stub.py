"""W4 — mcp_tools is a deliberate stub in the MVP (ADR-05 §5.2)."""

from __future__ import annotations

import pytest

from relay.harness.protocol import SignalConfig
from relay.harness.signaling.mcp_tools import detect_in_tool


def test_mcp_tools_strategy_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError) as ei:
        detect_in_tool("relay__handoff", SignalConfig(strategy="mcp_tools"))
    assert "not built in the MVP" in str(ei.value)
    assert "text_sentinels" in str(ei.value)
