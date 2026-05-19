# Discussion workflow

> The Discussion workflow is the alternative to the Build workflow. Use when
> the user wants to explore ideas, weigh tradeoffs, or design before
> committing to a build. The router (`SKILL.md`) decides which workflow
> applies per invocation.

## Discussion Workflow

The discussion workflow is for brainstorming, exploring options, teaching, weighing tradeoffs, and
building shared understanding. No code is modified. No worktrees, commits, or PRs. The output is
clarity — for the user and for future work.

### When to Use

Use this workflow when the user wants to:
- Brainstorm approaches to a problem before committing to one
- Understand tradeoffs between architectures, libraries, patterns, or designs
- Learn how something works (a concept, a part of the codebase, a technology)
- Explore "what if" scenarios — what would change if we used X instead of Y?
- Make a decision and wants structured input from multiple perspectives
- Establish context and shared understanding before starting real work

### How It Works

You are still the Lead Engineer. Your "team" is still the set of engineer and product-owner
lenses (see `team-structure.md`) — but in relay-v2's single-session MVP you work each lens
yourself, in sequence, rather than dispatching subagents. Instead of doing work, you are
**having a structured conversation with yourself on the user's behalf** — researching,
analyzing, debating each perspective, and presenting findings to the user so the user can
make informed decisions.

#### Step 1: Understand the Question

Before launching research, batch a few clarifying questions:

- **What's the decision?** "Should I use Postgres or SQLite?" is different
  from "Help me understand database options" — one has a decision point,
  the other is exploratory.
- **What are the constraints?** Scale, team expertise, existing infrastructure,
  timeline, budget — these shape which options are viable.
- **What do they already know?** Don't explain basics to an expert or assume
  expertise from a beginner. Pitch the discussion at the right level.
- **What matters most?** Simplicity, performance, maintainability, cost — the
  "best" option depends entirely on the optimisation target.

If you can answer a question by reading the codebase, do that instead of asking.

#### Step 2: Research

Work the topic from multiple angles, one lens at a time. The specific lenses
depend on the topic, but the pattern is always: **multiple perspectives,
grounded in evidence, not just opinions.** Apply each lens as a deliberate,
distinct pass:

**Technical deep-dive lens:**
- Research the technical options using `WebSearch` and `WebFetch` to read current documentation,
  benchmarks, comparison articles, and real-world experience reports
- If the discussion involves the current codebase, read the relevant code to understand the
  starting point — what exists today, what would need to change for each option
- Look for concrete data: performance benchmarks, adoption statistics, maintenance burden,
  known limitations, compatibility issues
- Check for recent developments — libraries get abandoned, APIs get deprecated, new options
  emerge. Training data may be stale.

**Implementation-analysis lens:**
- For each option under discussion, sketch out what the implementation would actually look
  like in this specific project. Not abstract theory — concrete files, changes, integration
  points.
- Identify hidden costs: migration effort, learning curve, dependency footprint, operational
  complexity, testing implications
- Look for gotchas: edge cases, failure modes, scaling cliffs, vendor lock-in

**Product-owner (context and fit) lens:**
- How does each option fit the project's goals, the user's constraints, and the team's
  capabilities?
- What are the second-order effects? If we choose option A, what does that make easier or
  harder down the road?
- Are there organizational/process implications? (e.g., "this requires a different deployment
  model" or "this adds an external dependency we'd need to monitor")

You don't always need all three lenses. For a simple "X vs Y" comparison, two may suffice.
For a complex architectural question, you might work four or five covering different facets.
Use judgment — match the research depth to the question's complexity.

#### Step 3: Synthesize and Present

As Lead Engineer, synthesize the research into a structured discussion. Present it directly
to the user in the conversation (not as a file — this is a conversation, not a deliverable).

**Structure the discussion as:**

1. **Context** — Briefly restate the question and constraints to confirm shared understanding.

2. **Options** — For each viable option:
   - What it is and how it works (at the right level for this user)
   - Advantages — be specific, cite evidence (benchmarks, docs, real examples)
   - Disadvantages — be equally specific and honest
   - What it would look like in this project — concrete, not abstract
   - Who it's best for / when it shines

3. **Comparison** — A direct head-to-head on the dimensions that matter most to the user.
   Use a table if there are 3+ options and 3+ dimensions. Use prose for simpler comparisons.

4. **Recommendation** — If the research points clearly toward one option given the user's
   constraints, say so and explain why. If it's genuinely a toss-up, say that instead —
   don't manufacture a false recommendation. If you need more information from the user to
   make a recommendation, say what you'd need to know.

5. **Open questions** — Things the user should think about that the research couldn't resolve.
   Things that depend on future decisions. Risks to watch for with any option.

**Tone:**
- Be a thoughtful advisor, not a salesperson. Present tradeoffs honestly.
- Disagree with the user if the evidence warrants it — "I know you're leaning toward X, but
  here's why Y might be better for your situation."
- Don't hedge everything. Where the evidence is clear, be direct. Where it's genuinely
  uncertain, say so.
- Teach when appropriate. If the user is learning, explain the "why" behind things, not just
  the "what."

#### Step 4: Discuss

After presenting, the conversation continues. The user will ask follow-up questions, push
back on points, want to go deeper on specific options, or raise new considerations. This is
the core of the workflow — it's interactive, not a one-shot report.

**During follow-up:**
- Open an additional research pass when the user raises something you don't have good
  information on. Don't guess — go find out.
- If the user's question is about the current codebase ("but would that work with our existing
  auth setup?"), read the code before answering.
- If the discussion reveals the question is actually about something different than originally
  framed, acknowledge the reframe and adjust.
- Keep the discussion grounded in the user's actual project and constraints, not hypothetical
  best practices.

#### Step 5: Capture (optional)

- **Decision made:** offer to save it as a brief document in
  `$RELAY_RUN_DIR/discussions/` (e.g. `YYMMDD-database-selection.md`) —
  question, options, decision, reasoning. Becomes context for later runs.
- **Exploratory:** don't force a written artifact. The conversation was the value.
- **Discussion leads to work:** if the user transitions to "ok, let's do it,"
  ask whether to switch to the build workflow with the discussion as context.

### What This Workflow Does NOT Do

No code, test, or doc changes. No worktrees, branches, test runs, lint, eval
reports, plans, commits, merges, or pushes. If the user starts asking for
changes mid-discussion, ask: "Switch to the build workflow, or still exploring?"
