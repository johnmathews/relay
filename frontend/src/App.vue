<script setup lang="ts">
import { RouterLink, RouterView } from 'vue-router'
</script>

<template>
  <div class="app-shell">
    <nav class="app-nav">
      <RouterLink
        to="/"
        class="app-nav__brand"
      >
        relay
      </RouterLink>
      <!-- Nav placeholder — populated by later Phase 4 units (W2+). -->
    </nav>
    <main class="app-main">
      <!--
        Key the view by full path so a param-only navigation
        (e.g. /runs/run-1 → /runs/run-2, which Vue Router would
        otherwise satisfy by REUSING the component instance) remounts
        it instead. This gives each run/project a fresh setup:
        module-scope guards (RunDetailView's `opened`) reset, the
        per-source UI store resolves cleanly in a fresh setup scope
        (no composable-in-computed reuse hazard), and no SSE
        EventSource is carried across runs.
      -->
      <RouterView v-slot="{ Component, route }">
        <component
          :is="Component"
          :key="route.fullPath"
        />
      </RouterView>
    </main>
  </div>
</template>
