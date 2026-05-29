"""Phase-5 MCP server (spec §8, ADR-27).

A FastMCP server whose seven tools are thin adapters over the single
in-process :class:`~relay.core.RelayCore` — the same service layer
that backs the REST routes (ADR-07/15). No proxying, no new core
capability. Mounted at ``/mcp`` by :mod:`relay.app`.
"""

from __future__ import annotations

from relay.mcp.server import create_mcp_server

__all__ = ["create_mcp_server"]
