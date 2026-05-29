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

## Delivery to the harness

There is no install step. Relay's pi harness injects the bundled
`pi/` variant into every pi spawn via `pi --skill <bundled-path>` —
the path resolves to the wheel-bundled `relay/skills/engineering-team/pi/`
in deployed installs and the repo-root `skills/engineering-team/pi/` in
editable installs. Override with `RELAY_PI_SKILLS=<path[:path...]>`
or opt out entirely with `RELAY_PI_SKILLS=`. ADR-44 records the
rationale (the earlier `relay install-skill` command was writing to
`.claude/skills/`, a Claude Code discovery root pi never reads).
Operational details: `docs/skills.md`.
