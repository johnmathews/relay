# Plan — skill-variants restructure

**Status:** ready to execute
**Date:** 2026-05-21
**Source proposal:** `docs/proposals/skills-harness-variants.md`
**Independent of:** `docs/plans/2026-05-21-fanout-join-9a.md` (different layer)

## Goal

Move the bundled `engineering-team` skill into a `pi/` subdirectory to make
"this skill = the pi variant" structurally explicit rather than implicit,
and add a `--harness` flag to `relay install-skill` so a future second
variant has a place to land. No behaviour change for any agent that uses
the installed skill — the destination path and contents stay byte-for-byte
identical to today.

Bundle this with a real bug fix in `install_skill`: the current
`shutil.copytree(src, target)` will silently drop the new variant-selector
README.md once the source layout is nested.

## Locked decisions (from the discussion)

- **Layout:** `skills/engineering-team/{README.md, pi/{SKILL.md, phases/, references/}}`. No empty `claude-code/` scaffold.
- **CLI flag:** `--harness pi` is the default; `--harness <unknown>` errors with a message naming available variants.
- **Install target unchanged:** `~/.claude/skills/engineering-team/`. The harness suffix exists only at the bundle layer; agents continue to load `engineering-team`, not `engineering-team-pi`.
- **install_skill fix:** refactor to copy the variant directory's contents into target **and** copy the parent `README.md` if present. The README is a human-readable variant selector; agents don't load it but humans inspecting the install do.
- **No upstream entanglement:** `install_skill` is 100% relay-owned (`src/relay_v2/cli/install_skill.py`); pi never sees it. Refactor freely.

## File-by-file changes

### Move (history-preserving)

```
git mv skills/engineering-team/SKILL.md      skills/engineering-team/pi/SKILL.md
git mv skills/engineering-team/phases        skills/engineering-team/pi/phases
git mv skills/engineering-team/references    skills/engineering-team/pi/references
```

After the move, the working tree looks like:

```
skills/engineering-team/
  pi/
    SKILL.md
    phases/{phase-1-evaluation,phase-2-planning,phase-3-development,phase-4-wrap-up}.md
    references/{sentinels,team-structure,workflows,worktree,discussion,general-guidelines}.md
```

### Create

**`skills/engineering-team/README.md`** (~40 lines, human-only — never loaded by an agent).
Sections:
- What this skill does (one paragraph: 4-phase evaluate → plan → develop → wrap-up cycle, sentinel-driven handoff).
- Variant model (one paragraph: why variants exist, what's in `pi/`).
- Installation (one paragraph: `relay install-skill` defaults to `pi`; future variants via `--harness <name>`).
- Pointer to ADR-33 for design rationale.

### Edit — `skills/engineering-team/pi/references/sentinels.md`

One sed-style change at the bottom of the file:

- Line ~123: `See: skills/engineering-team/references/sentinels.md`
- After: `See: skills/engineering-team/pi/references/sentinels.md`

This is the **only** repo-rooted path string anywhere in the skill tree (verified by Agent 3 — every other internal reference is relative).

### Edit — `src/relay_v2/cli/install_skill.py`

```python
def skill_source_dir(harness: str = "pi") -> Path:
    """Locate the bundled skill variant tree."""
    pkg_root = Path(__file__).resolve().parent.parent
    packaged = pkg_root / "skills" / SKILL_NAME / harness
    if packaged.is_dir():
        return packaged
    repo_root = Path(__file__).resolve().parents[3]
    source = repo_root / "skills" / SKILL_NAME / harness
    if source.is_dir():
        return source
    # Discover available variants for the error message.
    for base in (pkg_root / "skills" / SKILL_NAME,
                 repo_root / "skills" / SKILL_NAME):
        if base.is_dir():
            variants = sorted(p.name for p in base.iterdir() if p.is_dir())
            raise FileNotFoundError(
                f"skill variant {SKILL_NAME}/{harness!r} not found. "
                f"Available variants: {variants or '(none)'}"
            )
    raise FileNotFoundError(
        f"bundled skill {SKILL_NAME!r} not found (looked under "
        f"{pkg_root / 'skills'} and {repo_root / 'skills'})"
    )


def install_skill(
    *, project: Path | None = None, force: bool = False, harness: str = "pi"
) -> tuple[Path, Path | None]:
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
    # The variant-selector README sits one level above the variant dir.
    # Agents don't load it, but humans inspecting the install benefit.
    if parent_readme.is_file():
        shutil.copy2(parent_readme, target / "README.md")
    return target, backup


def main(args: argparse.Namespace) -> int:
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
```

### Edit — CLI parser (the `install-skill` subparser; find via grep `install-skill`)

```python
parser_install.add_argument(
    "--harness", default="pi",
    help="Skill variant to install (default: pi). "
         "Errors if the named variant does not exist."
)
```

### Tests

**`tests/cli/test_install_skill.py`** — additive:

- `test_default_harness_is_pi`: `install_skill()` resolves to `skills/engineering-team/pi/`.
- `test_explicit_harness_pi`: `install_skill(harness="pi")` same as default.
- `test_unknown_harness_errors`: `install_skill(harness="claude-code")` raises `FileNotFoundError` whose message contains `"pi"` (the available variant).
- `test_install_includes_parent_readme`: after `install_skill()`, target contains both `SKILL.md` and `README.md`. The README's content matches `skills/engineering-team/README.md` byte-for-byte.
- Existing tests: path assertions updated to expect `pi/` in the source path (where they look at `skill_source_dir()`); destination assertions stay the same (target is still `~/.claude/skills/engineering-team/`).

**`tests/skills/test_skill_structure.py`** — mechanical path prefix:

- Constants like `PHASE_DOCS = ["phases/phase-1-evaluation.md", ...]` become `PHASE_DOCS = ["pi/phases/phase-1-evaluation.md", ...]`.
- The base directory `SKILL = skill_source_dir()` now returns `engineering-team/pi/`, so any joins of the form `SKILL / "phases/..."` keep working — only the constants above (which assert the in-bundle layout) change.
- Verify after running the tests: assertion failures will pinpoint anything else that hardcoded the old shape.

### Docs

**`docs/skills.md`** — replace the "layout" section with the variant model:

```markdown
## Layout

skills/
  engineering-team/
    README.md              # variant selector (human-readable; not loaded by agents)
    pi/                    # variant for relay + pi harness
      SKILL.md
      phases/
      references/

A future second variant would live as a sibling, e.g. `claude-code/`.
The wheel's `force-include` (pyproject.toml) maps the whole `skills/`
tree into the package, so new variant subdirectories are automatically
bundled.
```

Add a `--harness` paragraph under the install-skill section.

**`CLAUDE.md`** — under "Toolchain", in the `relay install-skill` bullet,
add: "Skill source lives at `skills/engineering-team/<harness>/` (variant
directory, default `pi`); the variant model is documented in ADR-33."

**`docs/decisions.md`** — append ADR-33:

```markdown
## ADR-33 — Bundled skill variants live under per-harness subdirectories

**Status:** accepted (2026-05-21)
**Supersedes:** none

The `engineering-team` skill was ported in Phase 6 (ADR-28) with six
adaptations that made it pi-shaped, but the bundle layout didn't make
this visible — `skills/engineering-team/` looked harness-agnostic. This
ADR introduces a per-harness subdirectory convention so future variants
(claude-code, etc.) have a structural home.

**Decision:** bundled skills live at `skills/<name>/<harness>/`; a
shared `skills/<name>/README.md` describes the variant set for humans.
`relay install-skill --harness <name>` (default `pi`) selects the
variant. Install target path is unchanged (`~/.claude/skills/<name>/`):
the harness suffix exists only at the bundle layer.

**Rejected alternatives** (full discussion in proposal):
- Shared core + templated adapters: the Phase-6 adaptations are
  workflow-shape changes, not presentation. Two parallel docs are
  strictly easier to keep correct than one templated doc.
- Flat `engineering-team-pi/` peers: loses the "variants of one skill"
  grouping.
- Defer until a second variant exists: cheaper now, but mixes the
  structural change with the new variant when it arrives.

**Related:** ADR-04 (harness isolation), ADR-28 (Phase 6 skill port).
```

## Build sequence (commit-by-commit)

1. **`refactor(skills): move engineering-team into pi/ variant directory`**
   - `git mv` the three subtrees.
   - Update the one repo-rooted reference in `references/sentinels.md`.
   - Confirm `git log --follow` works on a moved file.

2. **`feat(cli): add --harness flag to install-skill (default pi)`**
   - Update `skill_source_dir` signature.
   - Update `install_skill` to also copy the parent README.
   - Update CLI parser.
   - Update tests.
   - **Verify:** `relay install-skill --force` produces the same target tree as before the rename **plus** a new `README.md` at the install root.

3. **`docs(skills): variant model + ADR-33`**
   - `docs/skills.md`, `CLAUDE.md`, `docs/decisions.md`, write the new
     `skills/engineering-team/README.md`.

Three commits, mergeable as one PR.

## Acceptance criteria

- [ ] `ls skills/engineering-team/` shows `README.md` and `pi/` only.
- [ ] `git log --follow skills/engineering-team/pi/SKILL.md` shows the
      pre-move history.
- [ ] `uv run relay install-skill --force` writes
      `~/.claude/skills/engineering-team/{SKILL.md, phases/, references/, README.md}`.
- [ ] `uv run relay install-skill --harness claude-code` errors with a
      message containing `"pi"` (the available variant).
- [ ] `uv run ruff check .` clean.
- [ ] `uv run mypy` clean.
- [ ] `uv run pytest` green (existing 194 + ~3 new install-skill tests).
- [ ] `frontend/ npm run check` green (frontend untouched, should be no-op).
- [ ] `docker build .` still succeeds (the wheel `force-include` picks up the new layout automatically — verify nothing was lost).

## Risks and what could go wrong

- **Wheel-bundled vs source-tree resolution drift.** The wheel
  `force-include` maps `skills/` → `relay_v2/skills/`. After the move
  the resolver looks for `relay_v2/skills/engineering-team/pi/`; verify
  this exists in the built wheel (`uv build && unzip -l dist/*.whl | grep engineering-team`).
- **Skill router's internal phase docs path.** SKILL.md's mapping for
  `RELAY_PHASE` → phase file is relative (`phases/phase-N-...md`).
  Confirmed safe by Agent 3 — but verify by running the engineering-team
  skill against a scratch project once installed.
- **`shutil.copy2` preserves metadata** — README modification time on
  the install copy will match the bundle's, not now. This is correct
  behaviour but worth knowing if anyone inspects mtimes.
- **Pre-existing user installs.** `--force` backs up the old install to
  `engineering-team.bak-<timestamp>`. No code change needed; the existing
  backup logic is intact.

## Out of scope

- Adding a second variant (`claude-code/`). This plan only creates the
  place where it would go.
- Harness auto-detection (the user picks via `--harness`).
- Multi-skill discovery (`engineering-team` remains the only bundled skill).
- Changes to the sentinel grammar, phase docs, or any agent-facing behaviour.

## Effort estimate

~½ day:

- Move + sed: 15 minutes.
- `install_skill` refactor + parser flag: 1 hour.
- Tests (~5 new, ~10 path updates): 1 hour.
- README.md + docs + ADR-33: 1 hour.
- Verification (full gate + manual install test): 30 minutes.
