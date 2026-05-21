"""``relay install-skill`` — deploy the bundled engineering-team skill.

The canonical skill source lives at the repo root in
``skills/engineering-team/<harness>/`` (spec.md §12, ADR-33). Each
harness gets its own subdirectory; ``pi/`` is the only variant today.
Two consumption modes must both work:

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


def _bundle_root() -> tuple[Path, Path]:
    """Return ``(packaged_root, source_root)`` for the bundled skills/<name> dir.

    The two candidates correspond to the wheel-installed location and the
    repo-root editable layout respectively. Either may not exist depending
    on install mode; resolvers below pick whichever is present.
    """
    pkg_root = Path(__file__).resolve().parent.parent  # …/relay_v2
    # parents: [0]=cli [1]=relay_v2 [2]=src [3]=<repo root>
    repo_root = Path(__file__).resolve().parents[3]
    return pkg_root / "skills" / SKILL_NAME, repo_root / "skills" / SKILL_NAME


def _available_variants() -> list[str]:
    """Discover the harness variants shipped with this build, for error messages."""
    for base in _bundle_root():
        if base.is_dir():
            return sorted(p.name for p in base.iterdir() if p.is_dir())
    return []


def skill_source_dir(harness: str = "pi") -> Path:
    """Locate the bundled skill variant tree.

    ``harness`` selects the per-harness subdirectory (today only ``pi``).
    Packaged (wheel, force-include) location is preferred; the repo-root
    source layout is the fallback for editable/source installs. Raises
    :class:`FileNotFoundError` if the named variant is not present,
    listing the available variants when the bundle itself exists.
    """
    packaged_root, source_root = _bundle_root()
    packaged = packaged_root / harness
    if packaged.is_dir():
        return packaged
    source = source_root / harness
    if source.is_dir():
        return source
    variants = _available_variants()
    if variants:
        raise FileNotFoundError(
            f"skill variant {SKILL_NAME}/{harness!r} not found. "
            f"Available variants: {variants}"
        )
    raise FileNotFoundError(
        f"bundled skill {SKILL_NAME!r} not found "
        f"(looked in {packaged} and {source})"
    )


def _target_dir(project: Path | None) -> Path:
    base = (project / ".claude") if project is not None else (Path.home() / ".claude")
    return base / "skills" / SKILL_NAME


def install_skill(
    *, project: Path | None = None, force: bool = False, harness: str = "pi"
) -> tuple[Path, Path | None]:
    """Copy the bundled skill variant to the target skills directory.

    Returns ``(target_dir, backup_dir_or_None)``. Raises
    :class:`FileExistsError` if the target exists and ``force`` is False,
    or :class:`FileNotFoundError` if the named variant is not bundled.
    The variant-selector README (``skills/<name>/README.md``, one level
    above the variant dir) is also copied into the target so humans
    inspecting the install can see the variant model — agents never load
    it.
    """
    src = skill_source_dir(harness=harness)
    parent_readme = src.parent / "README.md"  # variant-selector (optional)
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
    if parent_readme.is_file():
        shutil.copy2(parent_readme, target / "README.md")
    return target, backup


def main(args: argparse.Namespace) -> int:
    """Entry point for the ``install-skill`` subcommand."""
    project = Path(args.project).expanduser().resolve() if args.project else None
    try:
        target, backup = install_skill(
            project=project, force=args.force, harness=args.harness
        )
    except (FileExistsError, FileNotFoundError) as exc:
        print(f"relay install-skill: {exc}")
        return 1

    if backup is not None:
        print(f"Backed up existing skill → {backup}")
    print(f"Installed {SKILL_NAME} skill ({args.harness} variant) → {target}")
    return 0
