# engineering-team skill

A four-phase evaluate → plan → develop → wrap-up workflow that relay
chains across fresh harness sessions. The skill drives a single session
per phase, emits a small grammar of text sentinels
(`[[engteam:<verb> ...]]`) on phase transitions, and hands off via a
deliberately compressed prompt body so the next iter starts with the
context that matters and none of what doesn't.

This directory is a **variant selector**, not a SKILL.md. Agents never
read this file; humans inspecting the bundle do. The actual SKILL.md
for each harness lives one level down.

## Variants

Each subdirectory is a self-contained variant of the same skill,
shaped for one harness's quirks. Two variants of the same skill can
differ in workflow shape (subagent dispatch vs sequential lens-switching
is not a presentation tweak), so they live as parallel sources rather
than as a templated single doc. See ADR-33 in `docs/decisions.md` for
the design rationale and rejected alternatives.

- **`pi/`** — the variant for the relay + pi harness combination.
  Single-session execution (no Task-tool subagent dispatch),
  `.relay/runs/<run_id>/` for artifacts, relay-provisioned worktree
  (the skill verifies, never creates), inlined wrap-up gate (no
  `/done` or `/merge-push` slash commands), `uv run` examples. The
  six adaptations are documented in ADR-28.

A future second variant (e.g. `claude-code/`) would live as a sibling
directory.

## Installation

```
relay install-skill                        # default: --harness pi
relay install-skill --harness pi           # explicit
relay install-skill --project PATH         # → PATH/.claude/skills/engineering-team/
relay install-skill --force                # overwrite (existing copy backed up)
```

The install target is always `~/.claude/skills/engineering-team/`
(or `<project>/.claude/skills/engineering-team/` with `--project`):
the harness suffix exists only at the bundle layer, not at the
install destination. Operational details: `docs/skills.md`.
