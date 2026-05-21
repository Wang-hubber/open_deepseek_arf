<script setup lang="ts">
import { computed } from 'vue'
import { useI18n } from '@/composables/useI18n'
import type { TraceSession, TraceSummary } from '@/types'

const { t } = useI18n()

const props = defineProps<{
  sessions: TraceSession[]
  selectedId: string | null
  loading: boolean
  summary: TraceSummary | null
}>()

const emit = defineEmits<{
  select: [sessionId: string]
}>()

const visibleSessions = computed(() =>
  props.sessions.filter(s => s.session_id !== '__system__')
)

function formatMs(ms: number | undefined): string {
  if (!ms) return '-'
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)}s` : `${Math.round(ms)}ms`
}
</script>

<template>
  <aside class="ts-sidebar">
    <div class="ts-sidebar-header">
      <button class="ts-back" @click="$router.replace('/')">{{ t('trace.back') }}</button>
      <h2 class="ts-title">{{ t('trace.title') }}</h2>
    </div>
    <div v-if="summary" class="ts-summary">
      <div class="ts-stat"><span>{{ summary.total_sessions }}</span> {{ t('trace.sessions') }}</div>
      <div class="ts-stat"><span>{{ (summary.total_tokens || 0).toLocaleString() }}</span> {{ t('trace.tokens') }}</div>
      <div class="ts-stat up">{{ summary.thumbs_up || 0 }} 👍</div>
      <div class="ts-stat down">{{ summary.thumbs_down || 0 }} 👎</div>
    </div>
    <div v-if="loading && !sessions.length" class="ts-empty">{{ t('trace.loading') }}</div>
    <div v-else-if="!visibleSessions.length" class="ts-empty">{{ t('trace.noData') }}</div>
    <div v-else class="ts-session-list">
      <div
        v-for="s in visibleSessions" :key="s.session_id"
        class="ts-session-item"
        :class="{ active: selectedId === s.session_id }"
        @click="emit('select', s.session_id)"
      >
        <div class="ts-si-title">{{ s.title || s.session_id }}</div>
        <div class="ts-si-meta">
          {{ s.event_count }} {{ t('trace.events') }} · {{ (s.total_tokens || 0).toLocaleString() }} {{ t('trace.tokens') }} · {{ formatMs(s.total_duration_ms) }}
        </div>
      </div>
    </div>
  </aside>
</template>

<style scoped>
.ts-sidebar {
  border-right: 1px solid var(--border-glass);
  display: flex; flex-direction: column;
  background: rgba(7,7,16,0.8);
  overflow: hidden;
}
.ts-sidebar-header {
  padding: 16px; border-bottom: 1px solid var(--border-glass);
  display: flex; flex-direction: column; gap: 10px;
}
.ts-back {
  background: none; border: 1px solid rgba(255,255,255,0.1);
  color: var(--text-secondary); cursor: pointer; font-size: 12px;
  padding: 4px 12px; border-radius: var(--radius-sm); align-self: flex-start;
  transition: all var(--transition);
}
.ts-back:hover { color: var(--text-primary); background: rgba(255,255,255,0.06); }
.ts-title { margin: 0; font-size: 15px; }
.ts-summary {
  display: flex; flex-wrap: wrap; gap: 6px; padding: 10px 16px;
  border-bottom: 1px solid var(--border-glass);
  font-size: 11px;
}
.ts-stat { color: var(--text-secondary); }
.ts-stat span { color: var(--text-primary); font-weight: 700; }
.ts-stat.up { color: var(--success); }
.ts-stat.down { color: var(--danger, #ef4444); }
.ts-empty { padding: 24px 16px; color: var(--text-secondary); font-size: 13px; text-align: center; }
.ts-session-list { flex: 1; overflow-y: auto; }
.ts-session-item {
  padding: 12px 16px; cursor: pointer;
  border-bottom: 1px solid rgba(255,255,255,0.03);
  transition: background var(--transition);
}
.ts-session-item:hover { background: rgba(255,255,255,0.04); }
.ts-session-item.active { background: rgba(255,255,255,0.06); border-left: 3px solid var(--accent); }
.ts-si-title {
  font-size: 13px; font-weight: 600; margin-bottom: 3px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
}
.ts-si-meta { font-size: 11px; color: var(--text-secondary); }
</style>
