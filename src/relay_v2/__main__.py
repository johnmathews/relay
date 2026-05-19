"""`relay` CLI dispatch.

Phase 0 subset: ``relay serve`` and ``relay --version``. Phase 6 adds
``relay install-skill`` (docs/plan.md). The richer command set
(``start``, ``status``, ``cancel``) arrives in later phases.
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

    install = sub.add_parser(
        "install-skill",
        help="Install the bundled engineering-team skill",
    )
    install.add_argument(
        "--project",
        metavar="PATH",
        help="Install to PATH/.claude/skills/ instead of ~/.claude/skills/",
    )
    install.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing install (the old copy is backed up first)",
    )
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

    if args.command == "install-skill":
        from relay_v2.cli import install_skill

        return install_skill.main(args)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
