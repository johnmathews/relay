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
