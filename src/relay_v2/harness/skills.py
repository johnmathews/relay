"""Bundled-skill resolver for the pi harness.

Pi has a first-class skill system (`pi --skill <path>` loads a skill file
or directory). Relay ships the engineering-team skill inside the package
and points pi at the bundled tree directly — no per-project install, no
on-disk copy. Two install layouts must both resolve:

- **Editable / source install** (``uv sync`` + ``uv run`` on a checkout):
  the package is at ``src/relay_v2/``; the skill tree is the repo-root
  sibling ``../../skills/engineering-team`` relative to this file.
- **Built wheel**:
  ``[tool.hatch.build.targets.wheel.force-include]`` in ``pyproject.toml``
  maps the repo-root ``skills/`` tree into the wheel as
  ``relay_v2/skills/``, so the skill ships *inside* the package at
  ``relay_v2/skills/engineering-team``.

:func:`bundled_skill_dir` picks whichever is present.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["SKILL_NAME", "bundled_skill_dir"]

SKILL_NAME = "engineering-team"


def _candidates(harness: str) -> tuple[Path, Path]:
    """Return ``(packaged_root, source_root)`` for the bundled skill variant."""
    pkg_root = Path(__file__).resolve().parent.parent  # …/relay_v2
    # parents: [0]=harness [1]=relay_v2 [2]=src [3]=<repo root>
    repo_root = Path(__file__).resolve().parents[3]
    return (
        pkg_root / "skills" / SKILL_NAME / harness,
        repo_root / "skills" / SKILL_NAME / harness,
    )


def bundled_skill_dir(harness: str = "pi") -> Path:
    """Absolute path to the bundled engineering-team skill for ``harness``.

    Returns the wheel-bundled location if present, else the repo-root
    sibling. Raises :class:`FileNotFoundError` if neither exists — that
    means the skill tree wasn't shipped (broken install).
    """
    packaged, source = _candidates(harness)
    if packaged.is_dir():
        return packaged
    if source.is_dir():
        return source
    raise FileNotFoundError(
        f"bundled skill {SKILL_NAME!r}/{harness!r} not found "
        f"(looked in {packaged} and {source})"
    )
