"""Phase-6 ported-skill structural invariants (spec §12, ADR-13/14/28).

The Phase-6 named risk is *prose-quality regression* and the mandated
adaptations (single-session, `.relay/runs` paths, relay-provisioned
worktree, no `/done`/`/merge-push`) are easy to silently undo in a later
edit. These assertions lock the deliverable shape and the six deliberate
port adaptations so a regression fails the gate instead of shipping.

Static file checks — no ``RelayCore`` surface, so plain ``tmp_path``-free
``def test_*`` (ADR-24 ``asyncio_mode=auto`` only affects ``async`` tests).
"""

from __future__ import annotations

import pytest

from relay_v2.cli.install_skill import skill_source_dir

SKILL = skill_source_dir()

PHASE_DOCS = {
    "phases/phase-1-evaluation.md": "evaluation",
    "phases/phase-2-planning.md": "planning",
    "phases/phase-3-development.md": "development",
    "phases/phase-4-wrap-up.md": "wrap-up",
}
REFERENCES = [
    "references/sentinels.md",
    "references/team-structure.md",
    "references/workflows.md",
    "references/worktree.md",
    "references/discussion.md",
    "references/general-guidelines.md",
    "references/fanout.md",
]


def _read(rel: str) -> str:
    return (SKILL / rel).read_text()


def test_all_expected_files_present() -> None:
    assert (SKILL / "SKILL.md").is_file()
    for rel in [*PHASE_DOCS, *REFERENCES]:
        assert (SKILL / rel).is_file(), f"missing {rel}"


def test_skill_md_frontmatter_parses() -> None:
    text = _read("SKILL.md")
    assert text.startswith("---\n")
    fm = text.split("---", 2)[1]
    assert "name: engineering-team" in fm
    assert "description:" in fm


def test_sentinel_grammar_is_verbatim_v1() -> None:
    s = _read("references/sentinels.md")
    for verb in (
        "phase-start",
        "unit-start",
        "unit-done",
        "unit-abandoned",
        "handoff",
        "done",
        "pause-for-input",
    ):
        assert f"`{verb}`" in s, f"verb {verb} missing from sentinels.md"
    assert "[[engteam:prompt-start]]" in s
    assert "[[engteam:prompt-end]]" in s
    assert "[[engteam:<verb> <key=\"value\" ...>]]" in s


def test_each_phase_doc_emits_its_phase_start() -> None:
    for rel, phase in PHASE_DOCS.items():
        assert f'phase-start phase="{phase}"' in _read(rel)


def test_run_dir_path_migrated_to_relay_runs() -> None:
    """spec §3.3/§12: artifacts go to .relay/runs/<run_id>/, not v1's
    .engineering-team/runs/."""
    assert ".relay/runs/" in _read("SKILL.md")
    for rel in ["SKILL.md", *PHASE_DOCS, *REFERENCES]:
        assert ".engineering-team/runs" not in _read(rel), (
            f"{rel} still uses the v1 .engineering-team/runs path"
        )


def test_worktree_is_relay_provisioned_not_skill_created() -> None:
    """ADR-13: the orchestrator provisions the worktree; the skill must
    not run the v1 `git worktree add` instruction."""
    p3 = _read("phases/phase-3-development.md")
    assert "do NOT create one" in p3
    assert "relay-provisioned worktree" in p3
    # The v1 Phase-3 instruction was a `git worktree add -b
    # eng-...` command. It must not survive as an instruction.
    assert "git worktree add .claude/worktrees/eng-" not in p3
    assert "relay provisions" in _read("references/worktree.md").lower()


def test_single_session_adaptation_applied() -> None:
    """spec §12: each phase is one long session; coarse-grained
    parallelism arrived with the Phase-9b ``fanout`` closing sentinel
    (`references/fanout.md`). The in-iter lens / unit work stays
    sequential; the v1 ``Launch subagents in parallel`` per-lens pattern
    is still forbidden."""
    skill_md = _read("SKILL.md")
    assert "one long session per phase" in skill_md
    assert "references/fanout.md" in skill_md
    for rel in ["SKILL.md", *PHASE_DOCS, *REFERENCES]:
        assert "Launch subagents in parallel" not in _read(rel), (
            f"{rel} still has the v1 parallel-subagent dispatch instruction"
        )


def test_wrapup_inlines_gate_no_done_or_merge_push() -> None:
    """No `/done` or `/merge-push` under the pi harness — Phase 4
    performs their steps inline."""
    p4 = _read("phases/phase-4-wrap-up.md")
    assert "no `/done` or `/merge-push`" in p4
    assert "Run the `/done` skill to get all quality checks" not in p4
    assert "Run `/merge-push` to handle merging" not in p4
    # The inline gate still covers the steps /done would have run.
    for step in ("Security review:", "Lint + types:", "Journal:"):
        assert step in p4


@pytest.mark.parametrize("rel", [*PHASE_DOCS, *REFERENCES])
def test_no_empty_skill_docs(rel: str) -> None:
    assert len(_read(rel).strip()) > 200, f"{rel} looks like a stub"
