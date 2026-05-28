<script setup lang="ts">
// Right-pane body when selection.kind === 'artifact'. Renders the
// shared FileViewer against the run's artifact source. Phase 1: read
// only. Phase 4 (PauseBanner) wires `reviewPaths` to enable in-place
// editing when the file is a paused-review target.

import { computed } from 'vue'
import FileViewer from '@/components/files/FileViewer.vue'
import { runArtifactSource } from '@/lib/queries'

const props = defineProps<{
  runId: string
  path: string
}>()

const source = computed(() => runArtifactSource(props.runId))
</script>

<template>
  <div
    class="artifact-panel"
    data-testid="artifact-panel"
  >
    <FileViewer
      :source="source"
      :path="path"
    />
  </div>
</template>

<style scoped>
.artifact-panel {
  display: flex;
  flex-direction: column;
  gap: 0.6rem;
}
</style>
