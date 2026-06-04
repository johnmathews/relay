"""RELAY_* preamble builder (spec.md §12, ADR-14).

Every iter's prompt is the preamble followed by the body. The preamble
carries three canonical lines:

- ``RELAY_RUN_DIR`` — the per-run artifacts directory, ``<project_root>/
  .relay/runs/<run_id>/`` (spec.md §3.3). The skill writes every artifact
  under here; it is a *sibling* of the worktree, never nested in it.
- ``RELAY_PHASE`` — the phase the skill should resume in, taken from the
  **last** ``phase-start`` sentinel of the previous iter (sentinels.md:
  "the driver writes the last phase-start value seen"). Absent on iter 1
  and any iter where no phase has been declared yet — the skill then
  infers the phase from on-disk state, which is its documented fallback.
- ``RELAY_SENTINEL_REMINDER`` — a trailing nudge reminding the agent to
  emit a closing sentinel every turn (done / handoff / pause-for-input /
  fanout). Per ADR-53, this is a first line of defence for the loop's
  terminal-signal contract; WU3 recovery-iter and WU4 auto-pause are
  second and third lines of defence.

spec.md §12 names RUN_DIR and PHASE; SENTINEL_REMINDER was added per
ADR-53 to defend the loop's terminal-signal contract pre-emptively.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["build_preamble", "PREAMBLE_SEPARATOR"]

# Blank line between the preamble block and the prompt body.
PREAMBLE_SEPARATOR = "\n\n"


def build_preamble(run_dir: Path, phase: str | None) -> str:
    """Render the preamble block (no trailing separator).

    ``phase`` is ``None`` until the first ``phase-start`` is observed;
    the ``RELAY_PHASE`` line is then omitted entirely rather than emitted
    empty, so the skill's "no RELAY_PHASE → infer from disk" path triggers
    cleanly.

    The trailing ``RELAY_SENTINEL_REMINDER`` line (WU2 of the resilient-
    iter-close arc, ADR-53) nudges the agent toward emitting a closing
    sentinel every turn — defends the loop's terminal-signal contract
    pre-emptively so the recovery-iter path (WU3) and auto-pause
    fallback (WU4) are second and third lines of defence, not first.
    """
    lines = [f"RELAY_RUN_DIR: {run_dir}"]
    if phase:
        lines.append(f"RELAY_PHASE: {phase}")
    lines.append(
        "RELAY_SENTINEL_REMINDER: end every turn with a closing sentinel at "
        "column 0 — done / handoff / pause-for-input / fanout."
    )
    return "\n".join(lines)


def compose_prompt(run_dir: Path, phase: str | None, body: str) -> str:
    """Full prompt sent to the harness: preamble + separator + body."""
    return build_preamble(run_dir, phase) + PREAMBLE_SEPARATOR + body
