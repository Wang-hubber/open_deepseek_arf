<script setup lang="ts">
import { ref } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { TurnStart } from '@/types'

const { t } = useI18n()

defineProps<{
  turnStart: TurnStart
}>()

const expanded = ref(false)

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
</script>

<template>
  <div class="tsc-root">
    <div class="tsc-header" @click="expanded = !expanded">
      <span class="tsc-arrow" :class="{ open: expanded }">▶</span>
      <span class="tsc-icon">🚀</span>
      <span class="tsc-label">{{ t('trace.turnStart') }}</span>
      <span class="tsc-meta">{{ turnStart.events.length }} {{ t('trace.events') }} · {{ formatMs(turnStart.durationMs) }}</span>
    </div>
    <div v-if="expanded && turnStart.events.length" class="tsc-body">
      <div v-for="e in turnStart.events" :key="e.id" class="tsc-event">
        <span class="tsc-event-node">{{ e.node || (e as any).event_type || 'event' }}</span>
        <span v-if="e.duration_ms" class="tsc-event-dur">{{ formatMs(e.duration_ms) }}</span>
      </div>
    </div>
  </div>
</template>

<style scoped>
.tsc-root {
  margin-bottom: 12px;
  background: var(--bg-card);
  border: 1px solid var(--border-glass);
  border-radius: var(--radius-md);
  overflow: hidden;
}
.tsc-header {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 12px; cursor: pointer; user-select: none;
  color: var(--text-secondary); transition: background var(--transition);
  font-size: 12px;
}
.tsc-header:hover { background: var(--bg-hover); }
.tsc-arrow { font-size: 8px; transition: transform var(--transition); flex-shrink: 0; color: var(--text-muted); }
.tsc-arrow.open { transform: rotate(90deg); }
.tsc-icon { font-size: 13px; }
.tsc-label { font-weight: 600; }
.tsc-meta { margin-left: auto; font-size: 11px; color: var(--text-muted); font-family: monospace; }
.tsc-body { padding: 6px 12px 8px; border-top: 1px solid var(--border-light); }
.tsc-event {
  display: flex; align-items: center; gap: 8px;
  padding: 3px 0; font-size: 11px; color: var(--text-muted);
}
.tsc-event-node { font-family: monospace; font-size: 10px; flex: 1; }
.tsc-event-dur { font-family: monospace; font-size: 10px; }
</style>
