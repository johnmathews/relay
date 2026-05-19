"""``relay install-skill`` — deploy the bundled engineering-team skill.

The canonical skill source lives at the repo root in
``skills/engineering-team/`` (spec.md §12). Two consumption modes must
both work:

- **Source / editable install** (the only mode relay-v2 uses today —
  ``uv sync`` + ``uv run``): ``relay_v2`` resolves to ``src/relay_v2``;
  the skill is the repo-root sibling ``../../skills/engineering-team``
  relative to this file's package.
- **Built wheel**: ``[tool.hatch.build.targets.wheel.force-include]``
  in ``pyproject.toml`` maps the repo-root ``skills/`` tree into the
  wheel as ``relay_v2/skills/``, so the skill ships *inside* the
  package at ``relay_v2/skills/engineering-team``.

:func:`skill_source_dir` tries the packaged location first, then the
repo-root fallback, so a single resolver covers both. The command copies
that tree to ``~/.claude/skills/engineering-team/`` by default
(``--project PATH`` overrides to ``PATH/.claude/skills/...``); an
existing target is refused unless ``--force``, which backs the old copy
up first.
"""

from __future__ import annotations

import argparse
import shutil
from datetime import UTC, datetime
from pathlib import Path

__all__ = ["SKILL_NAME", "skill_source_dir", "install_skill", "main"]

SKILL_NAME = "engineering-team"


def skill_source_dir() -> Path:
    """Locate the bundled skill tree.

    Packaged (wheel, force-include) location is preferred; the repo-root
    source layout is the fallback for editable/source installs. Raises
    :class:`FileNotFoundError` if neither exists (the skill must always
    ship with the package).
    """
    pkg_root = Path(__file__).resolve().parent.parent  # …/relay_v2
    packaged = pkg_root / "skills" / SKILL_NAME
    if packaged.is_dir():
        return packaged
    # parents: [0]=cli [1]=relay_v2 [2]=src [3]=<repo root>
    repo_root = Path(__file__).resolve().parents[3]
    source = repo_root / "skills" / SKILL_NAME
    if source.is_dir():
        return source
    raise FileNotFoundError(
        f"bundled skill {SKILL_NAME!r} not found "
        f"(looked in {packaged} and {source})"
    )


def _target_dir(project: Path | None) -> Path:
    base = (project / ".claude") if project is not None else (Path.home() / ".claude")
    return base / "skills" / SKILL_NAME


def install_skill(
    *, project: Path | None = None, force: bool = False
) -> tuple[Path, Path | None]:
    """Copy the bundled skill to the target skills directory.

    Returns ``(target_dir, backup_dir_or_None)``. Raises
    :class:`FileExistsError` if the target exists and ``force`` is False.
    """
    src = skill_source_dir()
    target = _target_dir(project)
    backup: Path | None = None

    if target.exists():
        if not force:
            raise FileExistsError(
                f"{target} already exists; pass --force to overwrite "
                f"(the existing copy is backed up first)"
            )
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        backup = target.with_name(f"{target.name}.bak-{stamp}")
        shutil.move(str(target), str(backup))

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, target)
    return target, backup


def main(args: argparse.Namespace) -> int:
    """Entry point for the ``install-skill`` subcommand."""
    project = Path(args.project).expanduser().resolve() if args.project else None
    try:
        target, backup = install_skill(project=project, force=args.force)
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"relay install-skill: {exc}")
        return 1

    if backup is not None:
        print(f"Backed up existing skill → {backup}")
    print(f"Installed {SKILL_NAME} skill → {target}")
    return 0
