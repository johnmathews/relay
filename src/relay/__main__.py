"""`relay` CLI dispatch.

Today: ``relay serve`` and ``relay --version``. The richer command set
(``start``, ``status``, ``cancel``) arrives in later phases. The earlier
``relay install-skill`` subcommand was retired in 2026-05-25 — relay
now injects its bundled engineering-team skill directly into pi via
``--skill`` at spawn time (ADR-44), so per-project install is no longer
necessary or supported.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from relay.version import __version__

_LOCALHOST_BINDS = frozenset({"127.0.0.1", "localhost", "::1"})

_NON_LOCALHOST_WARNING = """\
WARNING: relay is binding to a non-localhost host (RELAY_HOST={host}).
The MVP envelope (ADR-12) assumes a localhost bind; off-localhost exposes:
  - /api/system/browse lists arbitrary host directories (not sandboxed).
  - /api/runs/... exposes full run management with no auth.
  - SSE streams have no rate limit / auth.
  - The MCP /mcp mount has its own DNS-rebinding protection but
    inherits the same network reachability.
Proceeding anyway (warn, not refuse). Set RELAY_HOST=127.0.0.1 to silence.
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relay", description="relay v2 orchestrator")
    parser.add_argument(
        "--version", action="version", version=f"relay {__version__}"
    )
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("serve", help="Run the relay daemon")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "serve":
        import uvicorn

        from relay.config import get_settings

        settings = get_settings()
        if settings.host not in _LOCALHOST_BINDS:
            print(
                _NON_LOCALHOST_WARNING.format(host=settings.host),
                file=sys.stderr,
                end="",
            )
        uvicorn.run("relay.app:app", host=settings.host, port=settings.port)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
