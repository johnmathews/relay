"""relay v2 — Python orchestrator for chained agent sessions.

Phase 0 scaffold: package, FastAPI factory, env-driven config, and the
SQLite schema (spec.md §3.1). Harness, orchestrator, REST surface, MCP
server, and dashboard arrive in later phases (docs/plan.md).
"""

from relay_v2.version import __version__

__all__ = ["__version__"]
