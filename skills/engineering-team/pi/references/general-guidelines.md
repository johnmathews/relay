# General guidelines and triage

> Cross-cutting rules and the triage entry point. Loaded by the router when
> the user reports something urgent or when phase docs reference a guideline
> by name.

## General Guidelines

**Single-session discipline (v2):** relay-v2's MVP runs one long
session per phase with no subagent dispatch. The v1 "subagent
coordination" rules become **self-review discipline** — you are the
only reviewer, so the cross-checking that v1 distributed across agents
you must do yourself, deliberately:

- Work each role lens (see `team-structure.md`) as a distinct pass.
  Don't blur them into one undifferentiated read — the value of the
  structure is that it forces coverage of dimensions a single
  unstructured pass skips.
- Cross-check your own findings before incorporating them. When two
  observations about the same code conflict, investigate and resolve —
  don't pass a contradiction through unchallenged.
- A single session can anchor on its first hypothesis. When a
  non-obvious conclusion is load-bearing for a recommendation, verify
  it against code or an external source before relying on it. "It
  seemed right on the first read" is not verification.
- Verify any URL, GitHub issue, or CVE before you cite it (`WebFetch`
  it). If a fact came from training data rather than a fetched page,
  say so. Don't fabricate citations.

**Quality bar:**
- No claims you can't ground in code, tests, or a fetched page. Cite file paths and
  line numbers; quote actual output.
- Be specific. "The `parse_config()` function on `config.py:23` doesn't handle
  malformed YAML — it throws an unhandled exception" beats "the code could be more
  robust."
- Label findings **[VERIFIED]** (you ran the code) or **[SUSPECTED]** (inferred from
  reading) — the distinction tells the user what to act on now vs. investigate.
- Describe what tests **cover**, not just that they pass. For changes touching
  persistent data, IO, or unexercised code paths, name what's covered AND what isn't.
  "All tests pass" is verification of the destination, not the journey.

**Visual / UI verification (Playwright):** Use Playwright MCP tools when the project
serves browser pages (frontend framework or server-side templates). The
UI-verification lens (Engineer 5 in the phase docs) and the Phase 3 implementation
step have the specifics — start the backend
too if the frontend depends on one, navigate the key pages, click through the
changed flows, check `browser_console_messages` and `browser_network_requests` for
errors. Don't mark a UI work unit complete until you've visually confirmed it
behaves correctly. Skip for pure backend, CLI, or library projects.

**Scope:**
- Stay focused on what the project actually needs. Don't recommend rewrites just
  because you'd write it differently. Prioritize risk (bugs, security) over style.
- If the project is small and working fine, say so — not everything needs improvement.

## Triage (when the user reports something urgent)

If the clarifying questions surface something urgent — broken feature, prod bug,
deployment that doesn't work — do focused triage **before** the full evaluation.
You don't do a comprehensive review while production is down.

1. **Reproduce.** Run the failing code, hit the broken endpoint, read the real
   error or stack trace. If you can't reproduce, ask the user for the exact
   error. A diagnosis without seeing the real error is guessing.
2. **Check what changed.** `git log` around when it broke; the working→broken
   diff is often the fastest path to the root cause.
3. **Investigate the code path.** Trace from entry point to failure. Use
   `WebSearch` / `WebFetch` for context on the APIs involved, but the diagnosis
   must come from matching the code against the error you observed.
4. **Verify before reporting.** Don't present a hypothesis as a root cause.
   Confirm X actually explains the reproduced error before telling the user
   "the problem is X" — confident-but-wrong wastes time and erodes trust.
5. **Report:** what error, what root cause, what evidence, what fix. Note any
   architectural concern briefly — Phase 1 will examine it properly.
6. **Then proceed to Phase 1.** The triage fix becomes Work Unit 1 (Critical)
   in the Phase 2 plan; the full evaluation may surface additional context
   that changes the fix.

If nothing urgent, skip triage and go to Phase 1.
