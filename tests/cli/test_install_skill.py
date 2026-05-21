"""Phase-6 ``relay install-skill`` CLI tests (docs/plan.md, ADR-28).

``install_skill`` is filesystem CLI logic with no ``RelayCore`` surface,
so these use ``tmp_path`` directly (sync ``def test_*``; the
``asyncio_mode=auto`` config only affects ``async def`` tests).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from relay_v2.__main__ import build_parser
from relay_v2.cli import install_skill as mod


def test_skill_source_dir_resolves_to_real_tree() -> None:
    src = mod.skill_source_dir()
    assert src.is_dir()
    assert (src / "SKILL.md").is_file()
    assert (src / "phases" / "phase-1-evaluation.md").is_file()
    assert (src / "references" / "sentinels.md").is_file()


def test_default_harness_is_pi() -> None:
    """The no-argument call resolves to the pi variant directory (ADR-33)."""
    src = mod.skill_source_dir()
    assert src.name == "pi"
    assert src.parent.name == mod.SKILL_NAME


def test_explicit_harness_pi_matches_default() -> None:
    assert mod.skill_source_dir(harness="pi") == mod.skill_source_dir()


def test_unknown_harness_errors_with_available_variants() -> None:
    with pytest.raises(FileNotFoundError, match=r"pi") as exc:
        mod.skill_source_dir(harness="claude-code")
    assert "claude-code" in str(exc.value)


def test_install_includes_parent_readme(tmp_path: Path) -> None:
    """The variant-selector README at skills/<name>/README.md is copied
    into the install target alongside the variant contents."""
    target, _ = mod.install_skill(project=tmp_path, force=False)
    parent_readme = mod.skill_source_dir().parent / "README.md"
    if not parent_readme.is_file():
        pytest.skip("variant-selector README not bundled in this build")
    installed = target / "README.md"
    assert installed.is_file()
    assert installed.read_bytes() == parent_readme.read_bytes()


def test_install_to_project_copies_full_tree(tmp_path: Path) -> None:
    target, backup = mod.install_skill(project=tmp_path, force=False)
    assert backup is None
    assert target == tmp_path / ".claude" / "skills" / "engineering-team"
    assert (target / "SKILL.md").is_file()
    assert (target / "phases" / "phase-4-wrap-up.md").is_file()
    assert (target / "references" / "discussion.md").is_file()
    # Content is a faithful copy, not a stub.
    assert (target / "SKILL.md").read_text() == (
        mod.skill_source_dir() / "SKILL.md"
    ).read_text()


def test_install_default_target_is_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mod.Path, "home", lambda: tmp_path)
    target, backup = mod.install_skill(project=None, force=False)
    assert target == tmp_path / ".claude" / "skills" / "engineering-team"
    assert backup is None
    assert (target / "SKILL.md").is_file()


def test_existing_target_without_force_raises(tmp_path: Path) -> None:
    mod.install_skill(project=tmp_path, force=False)
    with pytest.raises(FileExistsError, match="--force"):
        mod.install_skill(project=tmp_path, force=False)


def test_force_backs_up_and_overwrites(tmp_path: Path) -> None:
    target, _ = mod.install_skill(project=tmp_path, force=False)
    sentinel = target / "STALE_MARKER.txt"
    sentinel.write_text("old install")

    target2, backup = mod.install_skill(project=tmp_path, force=True)

    assert target2 == target
    assert backup is not None
    assert backup.is_dir()
    # The stale marker survived in the backup, and is gone from the
    # freshly-installed tree.
    assert (backup / "STALE_MARKER.txt").read_text() == "old install"
    assert not sentinel.exists()
    assert (target2 / "SKILL.md").is_file()


def test_main_success_and_failure_exit_codes(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()

    ok = parser.parse_args(
        ["install-skill", "--project", str(tmp_path), "--force"]
    )
    assert mod.main(ok) == 0
    assert "Installed engineering-team skill" in capsys.readouterr().out

    # Second run without --force → exit 1, helpful message.
    again = parser.parse_args(["install-skill", "--project", str(tmp_path)])
    assert mod.main(again) == 1
    assert "already exists" in capsys.readouterr().out


def test_parser_wires_install_skill_flags() -> None:
    ns = build_parser().parse_args(
        ["install-skill", "--project", "/tmp/x", "--force"]
    )
    assert ns.command == "install-skill"
    assert ns.project == "/tmp/x"
    assert ns.force is True
    # --harness defaults to pi when omitted (ADR-33).
    assert ns.harness == "pi"


def test_parser_accepts_explicit_harness_flag() -> None:
    ns = build_parser().parse_args(["install-skill", "--harness", "pi"])
    assert ns.harness == "pi"


def test_main_unknown_harness_exits_nonzero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parser = build_parser()
    ns = parser.parse_args(
        ["install-skill", "--project", str(tmp_path), "--harness", "claude-code"]
    )
    assert mod.main(ns) == 1
    out = capsys.readouterr().out
    assert "claude-code" in out
    assert "pi" in out
