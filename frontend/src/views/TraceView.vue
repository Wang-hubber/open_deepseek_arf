<script setup lang="ts">
import { ref, onMounted, watch } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from '@/composables/useI18n'
import { useTrace } from '@/composables/useTrace'
import type { FeedbackItem } from '@/types'
import TraceSidebar from '@/components/TraceSidebar.vue'
import TraceStatsBar from '@/components/TraceStatsBar.vue'
import TraceTurnList from '@/components/TraceTurnList.vue'

const { t } = useI18n()
const router = useRouter()

const {
  sessions, events, summary, loading, structuredSession,
  fetchSessions, fetchSessionDetail, fetchSummary, fetchFeedback,
  exportTrace, exportStructured,
} = useTrace()

const selectedSession = ref<string | null>(null)
const feedback = ref<FeedbackItem[]>([])

onMounted(() => {
  fetchSessions()
  fetchSummary()
})

watch(selectedSession, async (sid) => {
  if (sid) {
    await fetchSessionDetail(sid)
    feedback.value = await fetchFeedback(sid)
  }
})

function handleExport() {
  if (selectedSession.value) exportTrace(selectedSession.value)
}

function handleExportStructured() {
  if (selectedSession.value) exportStructured(selectedSession.value)
}
</script>

<template>
  <div class="tv-layout">
    <TraceSidebar
      :sessions="sessions"
      :selected-id="selectedSession"
      :loading="loading"
      :summary="summary"
      @select="selectedSession = $event"
    />

    <main class="tv-main">
      <template v-if="!selectedSession">
        <div class="tv-placeholder">{{ t('trace.selectSession') }}</div>
      </template>
      <template v-else>
        <div class="tv-detail-header">
          <h3>{{ selectedSession }}</h3>
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

        <!-- Feedback -->
        <div v-if="feedback.length" class="tv-feedback">
          <h4>{{ t('trace.feedback') }}</h4>
          <div v-for="fb in feedback" :key="fb.id" class="tv-fb-item">
            <span class="tv-fb-rating">{{ fb.rating === 1 ? '👍' : '👎' }}</span>
            <span class="tv-fb-msg">{{ t('trace.feedbackMessage', { index: fb.message_index + 1 }) }}</span>
            <span v-if="fb.feedback_text" class="tv-fb-text"> — "{{ fb.feedback_text }}"</span>
          </div>
        </div>
      </template>
    </main>
  </div>
</template>

<style scoped>
/* ── Layout ── */
.tv-layout {
  display: grid;
  grid-template-columns: 280px 1fr;
  height: 100vh;
  background: var(--bg-primary, #0a0a1a);
  color: var(--text-primary);
}

/* ── Main ── */
.tv-main {
  padding: 20px 24px; overflow-y: auto;
  display: flex; flex-direction: column;
}
.tv-placeholder {
  margin: auto; color: var(--text-secondary); font-size: 14px;
}
.tv-detail-header {
  display: flex; align-items: center; justify-content: space-between;
  margin-bottom: 4px;
}
.tv-detail-header h3 { margin: 0; font-size: 14px; font-family: monospace; }
.tv-header-actions { display: flex; gap: 8px; }
.tv-empty { padding: 24px; color: var(--text-secondary); font-size: 13px; text-align: center; }

/* ── Feedback ── */
.tv-feedback {
  margin-top: 24px; padding-top: 16px;
  border-top: 1px solid var(--border-glass);
}
.tv-feedback h4 { margin: 0 0 10px; font-size: 13px; }
.tv-fb-item { padding: 4px 0; font-size: 12px; color: var(--text-secondary); }
.tv-fb-rating { margin-right: 6px; }
.tv-fb-text { color: var(--text-primary); font-style: italic; }

/* ── Buttons ── */
.sb-btn {
  background: none; border: 1px solid rgba(255,255,255,0.1);
  color: var(--text-secondary); cursor: pointer; font-size: 12px;
  padding: 4px 12px; border-radius: var(--radius-sm);
  transition: all var(--transition);
}
.sb-btn:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }

/* ── Responsive ── */
@media (max-width: 900px) {
  .tv-layout { grid-template-columns: 1fr; }
  .tv-main { padding: 16px; }
}
@media (min-width: 1600px) {
  .tv-layout { grid-template-columns: 340px 1fr; }
}
</style>
