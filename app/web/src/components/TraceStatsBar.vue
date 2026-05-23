<script setup lang="ts">
import { useI18n } from '@/composables/useI18n'

const { t } = useI18n()

defineProps<{
  totalTurns: number
  totalTokens: number
  totalDurationMs: number
}>()

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}

function formatTokens(n: number): string {
  if (n >= 1000) return `${(n / 1000).toFixed(1)}k`
  return String(n)
}
</script>

<template>
  <div class="tsb-root">
    <span class="tsb-chip">📊 {{ t('trace.overview') }}:</span>
    <span class="tsb-chip">{{ formatTokens(totalTokens) }} {{ t('trace.tokens') }}</span>
    <span class="tsb-chip">·</span>
    <span class="tsb-chip">{{ totalTurns }} Turns</span>
    <span class="tsb-chip">·</span>
    <span class="tsb-chip">{{ formatMs(totalDurationMs) }}</span>
  </div>
</template>

<style scoped>
.tsb-root {
  display: flex; align-items: center; gap: 8px;
  padding: 8px 0 12px;
  font-size: 12px; color: var(--text-secondary);
  font-family: monospace;
}
.tsb-chip { white-space: nowrap; }
</style>
