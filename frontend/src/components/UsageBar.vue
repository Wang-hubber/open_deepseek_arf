<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useUsage } from '@/composables/useUsage'
import { useI18n } from '@/composables/useI18n'
import { useAppStore } from '@/stores/app'

const { t } = useI18n()
const { summary, fetchSummary } = useUsage()
const appStore = useAppStore()

const expanded = ref(true)

onMounted(() => {
  fetchSummary('month')
})

watch(() => appStore.usageRefreshKey, () => {
  fetchSummary('month')
})

function formatTokens(n: number): string {
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M'
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K'
  return String(n)
}
</script>

<template>
  <div id="usage-bar" :class="{ collapsed: !expanded }">
    <div class="ub-header" @click="expanded = !expanded">
      <span class="ub-title">{{ t('status.usage') }}</span>
      <span class="ub-toggle">{{ expanded ? '▼' : '▲' }}</span>
    </div>
    <div v-if="expanded" class="ub-body">
      <template v-if="summary && summary.total_calls > 0">
        <div class="ub-stats">
          <span class="ub-stat">
            <strong>{{ formatTokens(summary.total_tokens) }}</strong> tokens
          </span>
          <span class="ub-stat">
            <strong>{{ summary.total_calls }}</strong> calls
          </span>
        </div>
        <div v-if="summary.by_model.length > 0" class="ub-models">
          <div v-for="m in summary.by_model.slice(0, 3)" :key="m.model_name" class="ub-model-row">
            <span class="ub-model-name">{{ m.model_name }}</span>
            <span class="ub-model-tokens">{{ formatTokens(m.total_tokens) }} / {{ m.calls }} calls</span>
          </div>
        </div>
      </template>
      <div v-else class="ub-empty">
        {{ t('common.noData') }}
      </div>
    </div>
  </div>
</template>

<style scoped>
#usage-bar {
  border-top: 1px solid var(--border-glass);
  background: var(--bg-panel);
  font-size: 12px;
  flex-shrink: 0;
  backdrop-filter: blur(8px);
}
#usage-bar.collapsed .ub-header { border-bottom: none; }
.ub-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 7px 20px; cursor: pointer; user-select: none;
  border-bottom: 1px solid var(--border-light);
  transition: background var(--transition);
}
.ub-header:hover { background: var(--bg-hover); }
.ub-title { font-weight: 600; color: var(--text-secondary); font-size: 12px; }
.ub-toggle { color: var(--text-muted); font-size: 9px; transition: transform var(--transition); }
.ub-body { padding: 10px 20px; }
.ub-stats { display: flex; gap: 20px; margin-bottom: 8px; }
.ub-stat { color: var(--text-muted); font-size: 12px; }
.ub-stat strong {
  color: var(--text-primary); font-family: 'JetBrains Mono', 'Fira Code', monospace;
  font-weight: 600;
}
.ub-models { display: flex; flex-direction: column; gap: 3px; }
.ub-model-row { display: flex; justify-content: space-between; }
.ub-model-name { color: var(--text-secondary); }
.ub-model-tokens {
  color: var(--text-muted);
  font-family: 'JetBrains Mono', 'Fira Code', monospace; font-size: 11px;
}
.ub-empty { color: var(--text-muted); font-style: italic; text-align: center; padding: 6px 0; }
</style>
