"""Phase-5 MCP server (spec §8, ADR-27).

A FastMCP server whose seven tools are thin adapters over the single
in-process :class:`~relay_v2.core.RelayCore` — the same service layer
that backs the REST routes (ADR-07/15). No proxying, no new core
capability. Mounted at ``/mcp`` by :mod:`relay_v2.app`.
"""

from __future__ import annotations

from relay_v2.mcp.server import create_mcp_server

__all__ = ["create_mcp_server"]
