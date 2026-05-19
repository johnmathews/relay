"""`relay` CLI dispatch.

Phase 0 subset (docs/plan.md): ``relay serve`` and ``relay --version``.
The richer command set (``start``, ``status``, ``cancel``,
``install-skill``) arrives in later phases.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence

from relay_v2.version import __version__


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

        from relay_v2.config import get_settings

        settings = get_settings()
        uvicorn.run("relay_v2.app:app", host=settings.host, port=settings.port)
        return 0

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
