<script setup lang="ts">
import { onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '@/composables/useI18n'
import { useTrace } from '@/composables/useTrace'
import TraceStatsBar from '@/components/TraceStatsBar.vue'
import TraceTurnList from '@/components/TraceTurnList.vue'

const { t } = useI18n()
const router = useRouter()

const {
  events, summary, loading, structuredSession,
  fetchSessionDetail, fetchSummary,
  exportTrace, exportStructured,
} = useTrace()

const SESSION_ID = 'default'

onMounted(async () => {
  await fetchSessionDetail(SESSION_ID)
  fetchSummary()
})

function handleExport() {
  exportTrace(SESSION_ID)
}

function handleExportStructured() {
  exportStructured(SESSION_ID)
}
</script>

<template>
  <div class="tv-layout">
    <main class="tv-main">
      <div class="tv-detail-header">
        <button class="sb-btn" @click="router.replace('/')">← {{ t('trace.back') }}</button>
        <h3>{{ t('trace.title') }}</h3>
        <div class="tv-header-actions">
          <button class="sb-btn" @click="handleExport">{{ t('trace.exportJson') }}</button>
          <button class="sb-btn" @click="handleExportStructured">{{ t('trace.exportStructured') }}</button>
        </div>
      </div>

      <div v-if="loading" class="tv-empty">{{ t('trace.loading') }}</div>

      <template v-else-if="structuredSession">
        <TraceStatsBar
          :total-turns="structuredSession.stats.totalTurns"
          :total-tokens="structuredSession.stats.totalTokens"
          :total-duration-ms="structuredSession.stats.totalDurationMs"
        />

        <TraceTurnList
          :turn-start="structuredSession.turnStart"
          :turns="structuredSession.turns"
        />
      </template>

      <div v-else class="tv-empty">{{ t('trace.noData') }}</div>
    </main>
  </div>
</template>

<style scoped>
.tv-layout {
  min-height: 100vh;
  background: var(--bg-primary, #0a0a1a);
  color: var(--text-primary);
}

.tv-main {
  max-width: 1100px;
  margin: 0 auto;
  padding: 20px 24px;
  display: flex; flex-direction: column;
}

.tv-detail-header {
  display: flex; align-items: center; gap: 16px;
  margin-bottom: 16px;
}
.tv-detail-header h3 { margin: 0; font-size: 14px; font-family: monospace; color: var(--text-primary); }
.tv-header-actions { display: flex; gap: 8px; margin-left: auto; }

.tv-empty {
  padding: 48px 24px; color: var(--text-secondary); font-size: 13px; text-align: center;
}

.sb-btn {
  background: none; border: 1px solid rgba(255,255,255,0.1);
  color: var(--text-secondary); cursor: pointer; font-size: 12px;
  padding: 4px 12px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.sb-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }

@media (max-width: 900px) {
  .tv-main { padding: 16px; }
}
@media (min-width: 1600px) {
  .tv-main { max-width: 1300px; }
}
</style>
