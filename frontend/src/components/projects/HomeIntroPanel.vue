<script setup lang="ts">
// Intro panel rendered on the hub page below the project grid. Three
// short sections introducing relay and the engineering-team skill.
// Pure presentational — no props, no state.
</script>

<template>
  <section
    class="intro"
    aria-labelledby="intro-title"
    data-testid="home-intro"
  >
    <h2
      id="intro-title"
      class="intro__title"
    >
      About Relay
    </h2>

    <div class="intro__grid">
      <article class="intro__card">
        <h3 class="intro__card-title">
          What is Relay?
        </h3>
        <p class="intro__card-body">
          Relay orchestrates long-running coding work by chaining
          short, focused agent sessions against a swappable headless
          harness (pi today). Instead of one giant context that
          drifts, each step runs in a fresh session that receives only
          a compact handoff describing what came before.
        </p>
      </article>

      <article class="intro__card">
        <h3 class="intro__card-title">
          How it works
        </h3>
        <p class="intro__card-body">
          Every action — assistant text, tool call, signal, iter
          boundary — is appended to a SQLite event store. The
          orchestrator drives one iter at a time; an explicit
          <code>[[engteam:done]]</code> / <code>handoff</code> /
          <code>pause-for-input</code> sentinel closes each iter and
          tells Relay whether to continue, stop, or wait for you.
          This dashboard tails the event store live over SSE.
        </p>
      </article>

      <article class="intro__card">
        <h3 class="intro__card-title">
          The engineering-team skill
        </h3>
        <p class="intro__card-body">
          A multi-role skill bundled into every pi spawn — no
          per-project install needed. It walks a project through
          <strong>evaluate → plan → develop → wrap-up</strong> using
          subagents (product owner, engineer, lead). Start any run
          with <code>/engineering-team …</code> to invoke it.
        </p>
      </article>
    </div>
  </section>
</template>

<style scoped>
.intro {
  display: flex;
  flex-direction: column;
  gap: 0.85rem;
  padding: 1.25rem 1.5rem;
  border: 1px solid var(--color-border);
  border-radius: 10px;
  background: var(--color-surface);
  box-shadow: 0 1px 2px var(--color-shadow);
}

.intro__title {
  margin: 0;
  font-size: 1.05rem;
  font-weight: 600;
  color: var(--color-text);
}

.intro__grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 1rem 1.25rem;
}

.intro__card {
  display: flex;
  flex-direction: column;
  gap: 0.4rem;
}

.intro__card-title {
  margin: 0;
  font-size: 0.92rem;
  font-weight: 600;
  color: var(--color-text);
}

.intro__card-body {
  margin: 0;
  font-size: 0.88rem;
  line-height: 1.5;
  color: var(--color-text-dim);
}

.intro__card-body code {
  font-family: var(--font-mono);
  font-size: 0.82rem;
  padding: 0.05em 0.35em;
  border-radius: 4px;
  background: var(--color-surface-hover);
  color: var(--color-text);
}

.intro__card-body strong {
  color: var(--color-text);
}
</style>
